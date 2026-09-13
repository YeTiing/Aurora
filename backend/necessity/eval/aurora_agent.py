"""真实 Agent —— 驱动 Aurora 宿主跑一个任务。

从 harness.py 拆出来的理由：这是唯一依赖外部系统（Aurora / LLM 供应商）
的文件，其余评测逻辑不该因为宿主接口变动而被牵连；也让「本机没有 key」
这一事实集中在一处，而不是散落在跑分逻辑里。

接入方式：起/复用 Aurora HTTP 服务 → POST /api/chat → 取轨迹事件。
走 HTTP 而不是 import：Aurora 是独立进程的宿主，评测不该把它的内部模块
当库用（版本耦合会非常脆）。

⚠️ 已知短板（详见交付报告）：Aurora 的轨迹落库接口
`Store.append_agent_events / query_agent_events` 在本仓库内**没有实现**
（core/index/trace.py 会调用，但 Store 侧不存在该方法）。因此本类目前
只能拿到 chat 响应（response / diffs / tokens），**拿不到 agent_events**。
EVAL.md §7.4 规定「缺了轨迹就无法归因，必须重跑」，所以真实跑分在埋点
补齐前不具备可报告性 —— 本类会在 meta 里显式标注事件缺失，绝不假装齐了。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from backend.necessity.eval.agents import AgentRunResult, AgentUnavailable, DEFAULT_TURN_LIMIT, LLM_KEY_ENV

DEFAULT_AURORA_ROOT = Path(r"D:\codex_Projects\Aurora")
DEFAULT_AURORA_PORT = 9876

# 宿主的对话入口是**根路径** `/chat`（backend/api/routes/chat.py:58），
# 不是 `/api/chat` —— 后者返回 404。此前写错会让每一次真实运行都变成
# 「HTTP Error 404」，而错误信息看起来像网络问题。
CHAT_PATH = "/chat"


def _turns_from_events(events: list[dict]) -> int:
    """从轨迹事件里数真实轮次（最大的 turn 编号）。

    为什么需要：`AgentResponse` 里没有 turns 字段，硬读会恒得 0，
    于是 §7.3 的早停判据永远不触发。轮次只能从事件里恢复。
    """
    turns = [int(e.get("turn") or 0) for e in (events or [])]
    return max(turns) if turns else 0


class AuroraAgent:
    """驱动 Aurora 跑一个任务的真实 Agent。"""

    name = "aurora"

    def __init__(self, aurora_root: Path | str | None = None,
                 port: int = DEFAULT_AURORA_PORT, timeout_sec: int = 900):
        self.root = Path(aurora_root or os.environ.get("AURORA_ROOT") or DEFAULT_AURORA_ROOT)
        self.port = int(os.environ.get("AURORA_PORT", port))
        self.timeout_sec = timeout_sec
        self._proc = None

    # ── 可用性检查（缺 key 必须给出可执行指引）──────────────────

    def available(self) -> tuple[bool, str]:
        if not (self.root / "run_server.py").exists():
            return False, (
                f"找不到 Aurora 宿主：{self.root}\n"
                "  设置环境变量 AURORA_ROOT 指向宿主仓库，或用 --aurora-root 指定。"
            )
        key = next((k for k in LLM_KEY_ENV if os.environ.get(k)), "")
        if not key:
            return False, (
                "未配置 LLM API key，无法驱动真实 Agent。请设置以下任一环境变量：\n"
                + "".join(f"  {k}\n" for k in LLM_KEY_ENV)
                + "  （PowerShell 示例：$env:AURORA_LLM_API_KEY=\"sk-...\"）\n"
                "  只想验证评测管线本身，可用 --agent scripted"
                "（产出的是假数据，不可报告）。"
            )
        return True, f"aurora@{self.root} (key={key})"

    def require_available(self) -> None:
        ok, why = self.available()
        if not ok:
            raise AgentUnavailable(why)

    # ── 服务生命周期 ─────────────────────────────────────────────

    def _base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # Aurora 的健康检查在根路径 `/health`（backend/api/routes/chat.py:53）。
    # 此前这里写的是 `/api/health` —— 它返回 404，于是 _healthy() 永远为假，
    # start() 每次都要等满 60 秒再抛 AgentUnavailable。这不是崩溃而是
    # 「评测永远起不来」，且报错信息指向「服务未就绪」，与真因（路径写错）
    # 完全无关。保留 `/api/health` 作为兜底以便宿主将来加别名。
    _HEALTH_PATHS = ("/health", "/api/health")

    def _healthy(self) -> bool:
        for path in self._HEALTH_PATHS:
            try:
                with urllib.request.urlopen(self._base() + path, timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                continue
        return False

    def start(self) -> None:
        """启动宿主（已健康则复用 —— §7.3 的「缓存」精神）。"""
        if self._healthy():
            return
        self._proc = subprocess.Popen(
            [sys.executable, "run_server.py"], cwd=str(self.root),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "AURORA_PORT": str(self.port)},
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self._healthy():
                return
            time.sleep(1)
        raise AgentUnavailable(f"Aurora 服务在 60s 内未就绪（{self._base()}）")

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception:
                pass
            self._proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ── 跑一个任务 ───────────────────────────────────────────────

    def run(self, *, task_text: str, repo: Path, arm: str, run_index: int,
            session_id: str, hooks: object, turn_limit: int = DEFAULT_TURN_LIMIT) -> AgentRunResult:
        # ⚠️ 合并进 Aurora 后适配层仍在**一起跑的那个进程**里（评测通过 HTTP
        # 驱动宿主，但本类是 in-process 调的）——不过真实场景下宿主是独立
        # 子进程，set_hooks 只影响本进程，挂载必须由宿主的 run_server 按
        # AURORA_NECESSITY 环境变量自己完成。这里保留调用是为了
        # 「同进程直连」（AuroraAgent 直接调 graph）的路径仍然生效。
        from backend.necessity import adapter as _adapter

        self.require_available()
        self.start()
        _adapter.set_hooks(hooks)         # 把本 arm 的能力挂进本进程

        body = json.dumps({
            "message": task_text, "session_id": session_id,
            "workspace": str(repo), "sandbox_mode": "full-access",
            "approval_mode": "never", "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            self._base() + CHAT_PATH, data=body,
            headers={"Content-Type": "application/json"}, method="POST")

        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            return AgentRunResult(status="error", error=f"Aurora 调用失败: {e}")
        except Exception as e:
            return AgentRunResult(status="error", error=f"{type(e).__name__}: {e}")

        elapsed = time.time() - started
        diffs = data.get("diffs") or []
        diff_text = "\n".join(str(d) for d in diffs) if isinstance(diffs, list) else str(diffs)
        events, note = self._collect_events(session_id)

        # ⚠️ `turns` **不在** AgentResponse 里（backend/api/models.py:20）——
        # 它只有 session_id/response/plan/diffs/tokens。
        # 这里此前 `int(data.get("turns") or 0)` 会恒为 0，于是 EVAL §7.3 的
        # 早停判据（raw_turns > turn_limit）永远不会触发 —— 静默失效。
        # 真实轮次只能从轨迹事件里数（每个 turn 编号去重）。
        raw_turns = int(data.get("turns") or 0) or _turns_from_events(events)
        # ⚠️ **这里不给 pass**。判定由 runner 跑验收测试得出（eval/verify.py）——
        # 「LLM 回了文本」不是任务成功的证据：
        #   - 它可能回复「我做不到」
        #   - 它可能改了文件但改错
        #   - 它可能什么都没改只解释了一通
        # 此前这里写的是 `status = "pass" if data.get("response") else "fail"`，
        # 结果是所有 arm 都会「通过」，主指标（破坏调用方次数）完全不可测。
        # 这里只给一个保守的起点：有回复 = 至少没崩，等验收测试覆盖它。
        status = "fail"
        error = ""
        if raw_turns > turn_limit:
            # 早停必须在这里判：宿主不会自己停，评测侧才是计 fail 的地方
            status, error = "timeout", f"超出轮次上限 {turn_limit}（实际 {raw_turns}）"

        meta = {"elapsed_sec": round(elapsed, 2), "trace_note": note,
                "hook_stats": _adapter.hook_stats()}
        if not events:
            # 没有轨迹就无法归因（§7.4）—— 显式标记，让 report 能把它排除
            meta["events_missing"] = True
        return AgentRunResult(
            status=status, turns=min(raw_turns, turn_limit),
            tokens=int(data.get("tokens") or 0), events=events,
            diff_text=diff_text, diff_stats={}, error=error, meta=meta,
        )

    def _collect_events(self, session_id: str) -> tuple[list[dict], str]:
        """从轨迹库取事件。落库接口缺失时返回空 + 明确说明（不假装有数据）。"""
        db = self.root / ".necessity" / "index.db"
        if not db.exists():
            return [], f"轨迹库不存在: {db}（宿主埋点未落盘）"
        try:
            from backend.necessity.index.store import Store
            from backend.necessity.index.trace import TraceStore

            store = Store(str(db))
            try:
                t = TraceStore(db=store)
                evs = t.events(session_id=session_id)
                if not evs:
                    return [], "轨迹为空：Store.append_agent_events 埋点尚未实现"
                return [{"session_id": e.session_id, "kind": e.kind, "turn": e.turn,
                         "ts": e.ts, "payload": e.payload} for e in evs], ""
            finally:
                store.close()
        except Exception as e:
            return [], f"读取轨迹失败: {type(e).__name__}: {e}"
