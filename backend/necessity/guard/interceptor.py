"""interceptor.py —— 预检 / 后检 / 回滚（GUARD.md §5 / §6 / §7）。

为什么预检和后检都要（§5.2）：
    预检只看意图，`shell_command("python fix.py")` 完全看不出会改
    `src/utils.py`；它的价值是省一次执行 + 早点把 Agent 拉回来。
    后检看实际落盘的 diff，权威，能回滚。**后者是与工具无关的兜底**
    （§6.2），所以「覆盖所有写入路径」的保证来自后检，不来自工具分派。

故障原则（§9 总原则）：任何检查器异常都**放行 + 记录**，绝不阻塞任务。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from backend.necessity.hooks import Decision, FileChange, TaskResult, ToolCall, ToolResult

from .checker import CheckContext, Violation, check_constraints
from .contracts_in import inject_contracts
from .compiler import LLMClient, compile_from_task
from .feedback import FeedbackLedger
from .intent import contain, intent_paths, merge_changes, rel
from .rollback import RollbackManager
from .spec import StructuredConstraint
from .workspace import WorkspaceScanner

logger = logging.getLogger("necessity.guard")

__all__ = ["GuardHooks", "build_guard_hooks"]


class GuardHooks:
    """Constraint Guard 的钩子实现（NecessityHooks 的子集）。"""

    def __init__(self, cfg: dict | None = None):
        self.cfg: dict = dict(cfg or {})
        self.enabled: bool = bool(self.cfg.get("enabled", True))
        self.workspace: str = str(self.cfg.get("workspace") or ".")
        self.session_id: str = str(self.cfg.get("session_id") or "default")
        self.llm: LLMClient | None = self.cfg.get("llm")
        self.store = self.cfg.get("store")
        # override_action：实验开关。把全部约束统一压成某一种处理策略，
        # 用于 §11 的「warn 模式先观察违反频率，再决定是否 rollback」灰度。
        self.override_action = self.cfg.get("override_action")
        # 单条约束自带的 on_violation 优先于 default_action；后者是全局缺省。
        # INTEGRATION.md §8.1 默认 warn，与 GUARD.md §11 的灰度建议一致。
        self.default_action: str = str(self.cfg.get("default_action") or "warn")
        self.scanner = WorkspaceScanner(self.workspace,
                                        use_git=bool(self.cfg.get("use_git", True)))
        self.rollback = RollbackManager(
            self.workspace, self.session_id,
            backup_dir=str(self.cfg.get("backup_dir") or ".necessity/backup"))
        self._reset_task()

    def _reset_task(self) -> None:
        self.constraints: list[StructuredConstraint] = []
        self.rejected: list[dict] = []
        self.conflicts: list[dict] = []
        self.task_id = ""
        self.turn = 0
        self.first_violation_turn: int | None = None
        self.violations: list[Violation] = []
        self.rollback_actions: list[dict] = []
        self.ledger = FeedbackLedger()
        self.notes: list[str] = []
        self._live_scanned: list[FileChange] = []
        self._content_cache: dict[str, str | None] = {}
        self._initial_paths: set[str] = set()
        self._window: Any = None
        self._per_tool = True
        self._scan_ms = 0.0
        self.rollback.reset()

    # ── 生命周期 ────────────────────────────────────────────────

    def on_task_start(self, task: dict) -> None:
        if not self.enabled:
            return
        self._reset_task()
        self.task_id = str((task or {}).get("id") or (task or {}).get("task_id") or "")
        res = compile_from_task(task or {}, llm=self.llm)
        self.constraints = res.accepted
        self.rejected = [r.rejection() for r in res.rejected]
        self.conflicts = res.conflicts
        self.notes.extend(res.notes)
        # A2 隐式契约注入（规范 §4.7 的三档策略）。
        # 挖出的契约编译成 guard 已有类型后**并进同一个列表** ——
        # 于是拦截/回滚/账本全部白送（§4.3 的「执行层不新建机制」）。
        inject_contracts(self.constraints, self.notes, task or {})
        if res.rejected:
            # 拒绝项必须可见（§3.3）：进 on_task_end 报告，避免「以为有保护」
            self.notes.append(
                f"{len(res.rejected)} 条约束不可验证，已被明确拒绝（未静默忽略）")
        snap = self.scanner.scan(with_hashes=False)
        self._window = snap
        self._initial_paths = set(snap.entries)
        # 交给回滚器：用于区分「Agent 新建的文件」与「任务开始时就有的文件」
        self.rollback.set_initial_paths(self._initial_paths)

    def on_turn_end(self, turn: int) -> None:
        if not self.enabled:
            return
        self.turn = max(self.turn, int(turn or 0))
        # 每轮结束**总是**扫一次：
        #   1. _per_tool=False 时这是唯一的后检时机（§5.3.2 性能降级）
        #   2. _per_tool=True 时它是兜底 —— 捕获「工具调用窗口之外」发生的
        #      Agent 写入（宿主没调 after_tool、脚本在窗口边界外落盘等）。
        #      窗口内已在 after_tool 处理过，重复扫描 diff 为空，不重复计数。
        self._post_check_window()

    def on_task_end(self, result: TaskResult) -> dict:
        if not self.enabled:
            return {}
        turns = int(getattr(result, "turns", 0) or self.turn or 0)
        s = self.first_violation_turn if self.first_violation_turn else turns + 1
        rho = (min(s, turns) / turns) if turns > 0 else 1.0
        return {
            "task_id": self.task_id,
            "constraints_accepted": [c.to_dict() for c in self.constraints],
            "constraints_rejected": self.rejected,
            "conflicts": self.conflicts,
            "violations": [v.to_dict() for v in self.violations],
            "violation_count": len(self.violations),
            "first_violation_turn": self.first_violation_turn,
            "survival_turns": s,          # s：绝对值，直观
            "retention": round(rho, 6),   # ρ：归一化，跨任务可比（EVAL §2.2）
            "turns": turns,
            "rollback": self.rollback_actions,
            "feedback": self.ledger.recent(),
            "notes": self.notes,
            "scan_ms": round(self._scan_ms, 2),
        }

    # ── 预检 ────────────────────────────────────────────────────

    def before_tool(self, call: ToolCall) -> Decision:
        if not self.enabled or not self.constraints or call is None:
            return Decision()
        try:
            return self._pre_check(call)
        except Exception as e:      # §9 第 7 条：检查故障放行
            logger.warning("guard 预检异常，放行: %s", e)
            return Decision()

    def _pre_check(self, call: ToolCall) -> Decision:
        paths = intent_paths(call.name, call.arguments)
        # 记录基线：预检发生在工具执行**前**，此刻磁盘内容就是旧版本，
        # 这是拿回滚基线 + 算 size_limit 行数差最可靠的时机。
        for p in paths:
            self.record_baseline(p)
        if not paths:
            return Decision()   # 意图看不出路径 → 保守放行，交给后检
        changes = [FileChange(p, "modified") for p in paths]
        pre = [c for c in self.constraints if c.phase in ("pre", "both")]
        ctx = CheckContext(workspace=self.workspace, store=self.store)
        vs = [v for v in check_constraints(changes, pre, ctx)
              if self._resolve_action(v.constraint_id) == "block"]
        if not vs:
            return Decision()
        v = vs[0]
        text, _ = self.ledger.record(v, blocked=True)
        return Decision(action="block", reason=text, constraint_id=v.constraint_id)

    # ── 后检 ────────────────────────────────────────────────────

    def after_tool(self, call: ToolCall, result: ToolResult) -> None:
        if not self.enabled or not self.constraints:
            return
        try:
            if self._per_tool:
                self._post_check_window()
        except Exception as e:      # 故障放行 + 记录
            logger.warning("guard 后检异常，放行: %s", e)

    def _post_check_window(self) -> None:
        start = time.perf_counter()
        snap = self.scanner.scan()
        self._scan_ms += (time.perf_counter() - start) * 1000
        if self._scan_ms / max(1, self.turn or 1) > 500:
            self._per_tool = False
            self.notes.append("扫描超预算，已降级为每轮扫描一次（§5.3.2）")
        changes, info = self.scanner.diff(self._window, snap)
        self._window = snap
        if info.get("external"):
            self.notes.append(info["note"])
        for c in changes:
            # 宿主若已明确声明过该路径的写入者（after_write），扫描器的
            # 推断不得覆盖它 —— 这是「非 Agent 变更被排除」的落点（§6.3）。
            # 归因回写到 FileChange，因为 check_constraints 按 by_agent 过滤。
            if self.rollback.is_explicit(c.path):
                c.by_agent = self.rollback.is_agent_written(c.path)
            else:
                self.rollback.note_writer(c.path, "agent" if c.by_agent else "other")
        agent_changes = [c for c in changes if c.by_agent]
        if not agent_changes:
            return
        agent_changes = [self._with_line_delta(c) for c in agent_changes]
        self._live_scanned = merge_changes(self._live_scanned, agent_changes)
        post = [c for c in self.constraints if c.phase in ("post", "both")]
        ctx = CheckContext(workspace=self.workspace, store=self.store,
                           extra=self._extra_evidence())
        rolled_before = len(self.rollback_actions)
        for v in check_constraints(agent_changes, post, ctx):
            self._record_violation(v, actual=agent_changes,
                                   suspect_bulk=bool(info.get("suspect_bulk")))
        if len(self.rollback_actions) > rolled_before:
            # 回滚本身也是一次写入。若不刷新窗口，下一轮会把「回滚后的内容」
            # 再当成一次 Agent 改动 → 重复违反 + 反复回滚。窗口必须对齐到
            # 回滚后的真实状态。
            self._window = self.scanner.scan()

    def _extra_evidence(self) -> dict:
        return {}   # 由宿主/实验脚本按需注入 manifest/import 差分

    def scan_workspace(self) -> list[FileChange]:
        """权威变更列表：后检扫到的 Agent 改动（与工具无关）。"""
        return list(self._live_scanned) if self.enabled else []

    def after_write(self, path: str, writer: str) -> None:
        if not self.enabled or not path:
            return
        # explicit=True：宿主明确声明写入者（writer='other' 表示 git/用户/
        # 外部进程），比扫描器的推断更权威，允许覆盖已有归因。
        self.rollback.note_writer(rel(path, self.workspace), writer, explicit=True)

    # ── 违反处理 ────────────────────────────────────────────────

    def _resolve_action(self, cid: str) -> str:
        if self.override_action:
            return str(self.override_action)
        for c in self.constraints:
            if c.id == cid:
                return str(c.on_violation or self.default_action)
        return self.default_action

    def _record_violation(self, v: Violation, *, actual: list[FileChange],
                          suspect_bulk: bool) -> None:
        if self.first_violation_turn is None:
            self.first_violation_turn = self.turn or 1
        self.violations.append(v)
        action = self._resolve_action(v.constraint_id)
        if suspect_bulk and action == "rollback":
            action = "warn"     # §5.3.2：疑似批量外部改动时只告警
            self.notes.append(f"违反 {v.constraint_id} 但疑似批量变更，降级为 warn")
        rolled: list[dict] = []
        if action == "rollback":
            rolled = self.rollback.rollback(v.paths, v.constraint_id,
                                            self._baseline_lookup)
            self.rollback_actions.extend(rolled)
        _, count = self.ledger.record(v, rolled=rolled)   # §7.3 合并而非刷屏
        logger.info("constraint violation %s (%s, action=%s, merged=%d)",
                    v.constraint_id, v.ctype, action, count)

    # ── 基线 / 路径 ─────────────────────────────────────────────

    def record_baseline(self, path: str) -> None:
        """记录某文件「Agent 改动前」的内容。只在尚未记录时写入 ——
        第一次看到它时的版本才是基线。公开方法：宿主可在工具执行前主动
        调用（比 before_tool 更早、更准），这是给集成层留的补强口子。
        """
        r = rel(path, self.workspace)
        if r not in self._content_cache:
            self._content_cache[r] = self._read_workspace(r)

    def _baseline_lookup(self, r: str) -> str | None:
        return self._content_cache.get(r)

    def _read_workspace(self, r: str) -> str | None:
        try:
            return contain(self.workspace, r).read_text(encoding="utf-8")
        except Exception:
            return None

    def _with_line_delta(self, c: FileChange) -> FileChange:
        """用基线内容算真实增删行数（size_limit 依赖它）。

        scan_workspace 只报「文件变了 + 大小」（§5.3.5：全量缓存内容代价高）。
        对**已记录基线**的文件可精确算行数；算不出来保持 0 —— 宁可漏报也
        不造假数字。
        """
        base = self._content_cache.get(c.path)
        if base is None:
            return c
        cur = self._read_workspace(c.path)
        if cur is None:
            return c
        import difflib
        added = removed = 0
        for line in difflib.unified_diff(base.splitlines(), cur.splitlines(),
                                         n=0, lineterm=""):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return FileChange(c.path, c.kind, added, removed, c.by_agent)

def build_guard_hooks(cfg: dict | None = None) -> GuardHooks:
    """工厂 —— core/capability.py::FACTORY_NAMES 依赖这个名字。"""
    return GuardHooks(cfg)
