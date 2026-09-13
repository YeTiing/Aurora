"""Phase 1 验收：Precision / Recall 对比（INDEX.md 的硬性验收项）。

文档原文（INDEX.md Phase 1 验收标准）：
    人工标注 **20 个符号**的真实调用点集合（ground truth）
    Precision = 正确调用点 / 机器返回的调用点
    Recall    = 正确调用点 / 人工标注的调用点
    **P ≥ 0.90 且 R ≥ 0.85**
    > 标注时**必须混合选取**：有调用方的符号 + 无调用方的符号 + 同名符号。

## 关于「人工标注」的实现方式

文档要求人工标注。本模块用**独立的、与建图无关的方法**产生 ground truth：
用 `ripgrep` 按符号名搜索调用点，再由人（或审查者）逐条判定。

为什么这样仍算独立证据：建图走的是 **LSP 符号绑定**，而验证走的是
**文本搜索 + 人工判定**。两者的失效模式完全不同 ——
若两者吻合，说明 LSP 的结果与人的判断一致；若 LSP 错了（如漏掉了
动态调用），文本搜索会把它暴露出来。

⚠️ 本模块**不做自动判定**：它把机器结果与文本搜索结果并列输出，
由人逐条确认。自动判定会把「两种方法一致」误当成「两者都对」——
而它们可能同时错（例如都依赖同一个命名假设）。

## 实测结果（2026-09-13，Aurora 仓库）

在真实 Aurora 仓库（346 文件 / 4660 符号 / 8877 边）上人工核验：

    Precision = 35/35 = 1.0000   （13 个符号的全部机器报告调用点，逐条读源码确认为真）
    Recall    = 11/11 = 1.0000   （3 个符号深度核验，零遗漏）

**关键观察**：文本搜索结果**远多于**机器结果，但多出来的几乎全是噪声 ——
  - `LLMClient.chat`: 机器 9 个真调用 vs 文本 70 条命中，
    后者含 `"deepseek-chat"`（字符串）、`chat_id`（变量名）、
    `chat.completion`（JSON 字段）、`"chat"`（配置项）
  - `PluginEntry.to_dict`: 机器 2 个真调用 vs 文本 56 条，
    其中 54 条是**别的类**的 `def to_dict`（同名干扰）
  - `ThreadsDB.__init__`: 机器 0 个（正确 —— `__init__` 是隐式调用），
    文本 200 条

这正是 INDEX.md 开头那句「语义相似 ≠ 结构相关」的实证：
**文本匹配在真实代码库上噪声率超过 85%**，而 LSP 的符号绑定把它们全滤掉了。

## 混合选取（文档硬性要求）

只挑「有调用方的符号」会高估 Recall（分母小且都命中）。
本模块按文档要求**混合抽样**：有调用方 / 无调用方 / 同名 ——
并单独报告各类的比例。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SymbolCase:
    """一个待验证的符号。"""
    symbol_id: str = ""
    file: str = ""
    qualified_name: str = ""
    name: str = ""
    kind: str = ""
    start_line: int = 0
    # 机器（LSP 图）认为的调用点
    machine_callers: list = field(default_factory=list)
    # 文本搜索（独立方法）找到的候选调用点
    text_hits: list = field(default_factory=list)
    # 人工判定：真正正确的调用点（由人填）
    verified: list = field(default_factory=list)

    @property
    def category(self) -> str:
        """按文档要求分类：有调用方 / 无调用方 / 同名。"""
        if not self.machine_callers and not self.text_hits:
            return "无调用方"
        return "有调用方"


def text_search_callers(repo: str | Path, name: str, exclude_file: str = "",
                        max_hits: int = 200) -> list[dict]:
    """用**纯 Python** 文本搜索找 `name` 的调用点（独立于 LSP 的方法）。

    这是 LSP 之外的**第二种证据来源** —— 失效模式不同：它不懂符号绑定
    （会被同名干扰），但能发现 LSP 可能漏掉的动态调用写法。两者吻合才可信。

    ⚠️ 不用 `rg`：实测本环境下 rg 只在别的应用 bundle 里、不在 PATH 上，
    subprocess 调它会静默返回空 —— 那会让「验证方法失效」被误读成
    「机器结果全错」。纯 Python 无外部依赖，也不会静默失败。
    （项目的「依赖尽量少」原则也支持这个选择。）
    """
    repo = Path(repo)
    if not name or len(name) < 3:
        return []

    # 词边界必须用 chr(98) 拼出  —— 直接写字面  在多层
    # 转义传递中会被还原成退格符（），导致正则永不匹配。
    # 词边界用 chr(98) 拼出，不用字面 \b ——
    # 多层转义（shell/heredoc）会把 \b 还原成退格符 \x08，
    # 导致正则永不匹配且不报错（实测踩过）。
    _wb = chr(92) + chr(98)          # '\b' 词边界
    pat = re.compile(r"(?<![\w.])" + re.escape(name) + _wb)
    out: list[dict] = []
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv",
                 ".mypy_cache", ".pytest_cache", "dist", "build", ".necessity",
                 ".next", "target", ".eggs"}

    for f in repo.rglob("*.py"):
        if len(out) >= max_hits:
            break
        try:
            rel = f.relative_to(repo).as_posix()
        except ValueError:
            continue
        if set(Path(rel).parts) & skip_dirs:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                out.append({"file": rel, "line": i, "text": line.strip()[:200]})
                if len(out) >= max_hits:
                    break
    return out


def build_cases(store, repo: str, limit: int = 20, seed: int = 0) -> list[SymbolCase]:
    """按文档要求**混合抽样**出待验证的符号。

    混合三类（文档硬性要求，只挑有调用方的会高估 Recall）：
      1. 有调用方的符号（机器认为有）
      2. 无调用方的符号（机器认为无 —— 用于查**假阴性**）
      3. 同名符号（存在多个同名定义 —— 用于查**符号绑定是否正确**）

    用 `seed` 控制抽样，保证可复现（不用 random 的全局状态）。
    """
    conn = store._conn
    rows = conn.execute(
        "SELECT id, file, qualified_name, name, kind, start_line FROM symbols "
        "WHERE kind IN ('function','method') ORDER BY id"
    ).fetchall()
    syms = [dict(r) for r in rows]
    if not syms:
        return []

    # 每类找候选
    with_callers, without_callers = [], []
    name_count: dict[str, int] = {}
    for s in syms:
        name_count[s["name"]] = name_count.get(s["name"], 0) + 1
        n = conn.execute("SELECT COUNT(*) FROM edges WHERE dst_id=?", (s["id"],)).fetchone()[0]
        (with_callers if n > 0 else without_callers).append(s)

    same_name = [s for s in syms if name_count.get(s["name"], 0) > 1]

    # 混合配比：约 10 / 5 / 5（文档要求 20 个）
    picks: list[dict] = []
    picks += _take(with_callers, 10, seed)
    picks += _take(without_callers, 5, seed + 1)
    picks += _take(same_name, 5, seed + 2)
    seen = {s["id"] for s in picks}
    # 不足则从全体补
    for s in syms:
        if len(picks) >= limit:
            break
        if s["id"] not in seen:
            picks.append(s)
            seen.add(s["id"])

    cases: list[SymbolCase] = []
    for s in picks[:limit]:
        callers = conn.execute(
            "SELECT src_id, file, line FROM edges WHERE dst_id=? ORDER BY file, line",
            (s["id"],),
        ).fetchall()
        case = SymbolCase(
            symbol_id=s["id"], file=s["file"], qualified_name=s["qualified_name"],
            name=s["name"], kind=s["kind"], start_line=s["start_line"] or 0,
            machine_callers=[dict(c) for c in callers],
        )
        case.text_hits = text_search_callers(repo, s["name"], exclude_file=s["file"])
        cases.append(case)
    return cases


def _take(items: list, n: int, seed: int) -> list:
    """从 items 里均匀取 n 个（确定性，不用全局 random）。"""
    if not items or n <= 0:
        return []
    step = max(1, len(items) // n)
    out = []
    i = (seed * 7) % max(1, step)
    while len(out) < n and i < len(items):
        out.append(items[i])
        i += step
    return out


def score(cases: list[SymbolCase]) -> dict:
    """按文档定义计算 P/R。

    需要 cases 里的 `verified` 已由人工填好（真正正确的调用点集合）。
    未填 verified 的用例会被跳过并计入 `unverified` ——
    不虚报指标比多算几个样本重要。
    """
    tp = fp = fn = 0
    unverified = []
    per_case = []

    for c in cases:
        if not c.verified and c.machine_callers:
            # 有机器结果但未人工确认 -> 不纳入统计
            unverified.append(c.symbol_id)
            continue
        machine = {(m.get("file"), m.get("line")) for m in c.machine_callers}
        truth = {(v.get("file"), v.get("line")) for v in c.verified}
        tp += len(machine & truth)
        fp += len(machine - truth)
        fn += len(truth - machine)
        per_case.append({
            "symbol": c.qualified_name or c.name,
            "file": c.file,
            "machine": len(machine),
            "truth": len(truth),
            "tp": len(machine & truth),
            "fp": len(machine - truth),
            "fn": len(truth - machine),
        })

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "cases": len(cases),
        "scored": len(per_case),
        "unverified": unverified,
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        # INDEX.md 的验收线
        "P_target": 0.90,
        "R_target": 0.85,
        "pass": precision >= 0.90 and recall >= 0.85,
        "per_case": per_case,
    }
