"""git 历史裁判法 —— 规范 §4.6，A2 的 ground truth 来源。

## 它解决什么问题

规范把这条列为 A2 的**最大难点**（§4.4 第 4 点「无 ground truth」）：

> 难点：谁来判定「这个隐式约定确实存在且应该保留」？

**解法：用 git 历史做客观裁判。**

> 假设：如果某条约定真的存在，那么**违反它的提交往往后续被修复或被 revert**。

所以：**被 revert 的提交 / 紧跟修复的提交** = 客观的「违反事件」。

## 为什么这个假设可接受

规范原话：「`git log` **客观存在、无需人工标注、可重复**」。
人工标注的 ground truth 会引入标注者偏差且不可复现；git 历史不会。

## 必须如实报告的局限（规范反复强调）

    「不是每条违反都会被 revert → **这是召回率的下界，不是真值**」

所以本模块返回的字段名就叫 `recall_lower_bound`（不是 `recall`），
并且报告里必须带上这条声明。把下界当准确值会让人高估覆盖率。
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger("necessity.contract.judge")

# git 输出是 UTF-8，而 Windows 控制台默认 GBK —— 不显式指定编码会抛
# UnicodeDecodeError，异常被吞后返回空（这个坑在本仓库踩过多次）。
_GIT_KW = dict(capture_output=True, text=True, encoding="utf-8", errors="replace")

# 被 revert 的提交：subject 形如 `Revert "xxx"`。
_REVERT_RE = re.compile(r'^\s*Revert\s+"', re.I)

# 「紧跟修复的提交」的判据：**只看提交类型前缀**，不扫整个 subject。
#
# ⚠️ 实测踩过：扫全 subject 会把 `feat(necessity): ... 含 5 个实测缺陷修`
# 这类提交也算成 fix（中文描述里带「修」字）。而 feat 提交是**新增功能**，
# 不是「违反约定后的修复」—— 用它当违反事件会让召回率分母虚高、
# 匹配率虚低，整份评估失去意义。
#
# 所以只认 Conventional Commits 的类型前缀（`fix:` / `fix(scope):`），
# 这是客观、可复核的判据；描述里出现什么字都不影响。
_FIX_PREFIX_RE = re.compile(r"^\s*(fix|bugfix|hotfix|revert)\s*(\([^)]*\))?\s*:",
                            re.I)

# 没有类型前缀时**不用**模糊匹配兜底 —— 宁可漏掉，不可误判。
# 规范 §4.6 的前提是「客观、可重复」，模糊匹配两者都不是。


@dataclass
class ViolationEvent:
    """一次历史违反事件（客观事实，不是推断）。"""

    commit: str
    subject: str
    kind: str               # "revert" | "fix"
    files: list[str] = field(default_factory=list)


@dataclass
class JudgeResult:
    """git 历史裁判的结论。"""

    events: list[ViolationEvent] = field(default_factory=list)
    matched: int = 0
    recall_lower_bound: float | None = None
    false_positive_rate: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        return len(self.events)


def _git(args: list[str], repo: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=repo, timeout=60, **_GIT_KW)
        return (r.stdout or "") if r.returncode == 0 else ""
    except Exception as e:
        logger.warning("git %s 失败: %s", " ".join(args), e)
        return ""


def find_violation_events(repo: str, *, limit: int = 500) -> list[ViolationEvent]:
    """从 git 历史找出「违反事件」：被 revert 的提交 / 紧跟修复的提交。

    **纯客观**：只读 commit subject 与改动文件，不做任何语义推断。
    """
    raw = _git(["log", f"-{limit}", "--no-merges", "--pretty=format:%H%x09%s"], repo)
    if not raw:
        return []

    rows = []
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        rows.append((sha.strip(), subject.strip()))

    events: list[ViolationEvent] = []
    for sha, subject in rows:
        kind = ""
        if _REVERT_RE.search(subject):
            kind = "revert"
        elif _FIX_PREFIX_RE.search(subject):
            kind = "fix"
        if not kind:
            continue
        files = _changed_files(repo, sha)
        events.append(ViolationEvent(commit=sha[:8], subject=subject,
                                     kind=kind, files=files))
    return events


# 不是**代码**的文件，不该参与契约裁判。
#
# ⚠️ 实测发现：Aurora 自己的历史里，fix 提交的 files 混着
# `.aurora/fts5.db-shm`、`.necessity/index.db` 这类运行时产物
# （运行测试时被误提交的）。它们与「隐式行为约定」无关，
# 放进 files 集合会让**任意**候选的 scope 都"有可能"匹配上，
# 把误报率稀释成毫无意义的数字。
#
# 这类噪声在真实仓库里普遍存在（构建产物、锁文件、缓存），
# 所以过滤是必需的，不是为 Aurora 特判。
_NON_CODE_SUFFIXES = (
    ".db", ".db-shm", ".db-wal", ".lock", ".log", ".jsonl",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".map",
)
_NON_CODE_PREFIXES = (".aurora/", ".necessity/", "node_modules/", ".venv/",
                      "dist/", "build/", "__pycache__/")


def _is_code_file(path: str) -> bool:
    """是否是「与行为约定相关」的代码文件。

    只排除明确的产物/缓存；**保留**文档与配置（`README.md`、
    `.gitignore` 也可能是约定的载体，而且它们是纯文本、可在 diff 里读到）。
    """
    p = path.replace("\\", "/").strip()
    if not p:
        return False
    if p.startswith(_NON_CODE_PREFIXES):
        return False
    if any(seg == "__pycache__" for seg in p.split("/")):
        return False
    return not p.lower().endswith(_NON_CODE_SUFFIXES)


def _changed_files(repo: str, sha: str) -> list[str]:
    raw = _git(["show", "--name-only", "--pretty=format:", sha], repo)
    return [ln.strip() for ln in raw.splitlines()
            if ln.strip() and _is_code_file(ln.strip())]


def evaluate(candidates: list, repo: str, *, events=None,
             limit: int = 500) -> JudgeResult:
    """用历史事件评估契约集的召回率下界与误报率（规范 §4.6 第 4 步）。

       召回率（下界）= 挖出并匹配的契约数 / 已知违反事件数
       误报率        = 挖出但无历史证据支撑的契约数 / 总挖出数

    ⚠️ 两个数都**必须**被解读为估计：
      · 召回率是**下界**（不是每条违反都会被 revert）
      · 误报率是**上界**（没有历史证据不等于契约错 ——
        可能只是那块代码没出过事）
    两个偏差方向相反，所以报告里要一起给出，不能只报一个。
    """
    res = JudgeResult()
    if events is None:
        events = find_violation_events(repo, limit=limit)
    res.events = list(events)

    if not events:
        res.notes.append(
            "git 历史里没有找到 revert / fix 提交 —— 无法评估契约集质量。"
            "注意：这**不是**「契约都对」，而是「没有裁判数据」。")
        return res

    involved_files = {f for e in events for f in (e.files or [])}

    matched = 0
    supported = 0
    for c in candidates or []:
        scope = getattr(c, "guard_scope", None) or {}
        files = _files_of(scope)
        if files & involved_files:
            matched += 1
            supported += 1
        elif not files:
            # scope 里没有文件信息（如 call_chain 只给 root）——
            # 无法判断相关性。算作「不确定」而不是「未被支撑」。
            supported += 1

    total = len(candidates or [])
    res.matched = matched
    res.recall_lower_bound = round(matched / len(events), 4)
    res.false_positive_rate = round((total - supported) / total, 4) if total else None

    res.notes.append(
        f"召回率 {res.recall_lower_bound:.1%} 是**下界**（不是每条违反都会被 revert）；"
        f"误报率 {res.false_positive_rate:.1%} 是**上界**（无历史证据不等于契约错）。")
    res.notes.append(
        "裁判数据来自 git 历史（客观、无需人工标注、可重复），"
        "但样本量受限于该仓库的提交质量。")
    return res


def _files_of(scope: dict) -> set[str]:
    """从 scope 里抠出涉及的文件路径。

    各类型的 scope 形状不同（见 `guard/spec.py` 的约定），这里逐个处理。
    """
    out: set[str] = set()
    for key in ("symbols",):
        for s in scope.get(key) or []:
            if isinstance(s, dict) and s.get("file"):
                out.add(str(s["file"]))
    for key in ("files", "selectors", "patterns", "extra_patterns"):
        for v in scope.get(key) or []:
            if isinstance(v, str):
                # selectors/patterns 可能是文件名或 glob，能当路径用
                out.add(v)
    if scope.get("root_file"):
        out.add(str(scope["root_file"]))
    return out


__all__ = ["JudgeResult", "ViolationEvent", "evaluate", "find_violation_events"]
