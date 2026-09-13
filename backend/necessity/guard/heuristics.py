"""heuristics.py —— 离线启发式：不可验证识别 + 可验证措辞抽取。

拆出来的理由：这两组正则服务于**两种不同目的**，混在 compiler.py 里
容易让读者误以为它们是同一层判断。

  1. `reject_unverifiable(raw)` —— 识别「无客观谓词」的措辞，产出
     §3.3 要求的拒绝 + 改写建议。它只提升提示质量，**不决定能力边界**。
  2. `from_heuristics(items)` —— LLM 不可用时，从措辞里切出候选结构化约束。
     切错了会被 spec.validate 挡下（会明确报错，不会静默放行）。

真正的边界始终在 spec.py 的类型白名单，这里只是尽力而为的解析。
"""
from __future__ import annotations

import re

__all__ = ["reject_unverifiable", "from_heuristics"]

# 真正的「不可验证」判据（GUARD.md §3.2）：无客观谓词 / 无阈值 / 无检查器
_UNVERIFIABLE_HINTS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"优雅|简洁|漂亮|可读性|美观|clean|elegant|beautiful"),
     "「优雅/美观」无客观谓词，无法编译为确定性判定",
     "改为可验证形式，例如「不新增超过 20 行」或「通过 ruff check」"),
    (re.compile(r"尽量少改|尽可能少|越少越好|minimal(ly)? change|as little as possible"),
     "「尽量」没有阈值，谓词不成立",
     "给出数字阈值，例如「改动不超过 50 行」（size_limit）"),
    (re.compile(r"不要破坏现有功能|不引入\s*bug|保证正确|功能正常"),
     "「不破坏功能」需要操作化为具体测试，否则无定义",
     "改为「指定测试 X 必须仍通过」（test_preserved）"),
    (re.compile(r"风格一致|保持一致风格|code style consistent|遵循.{0,6}风格"),
     "「风格一致」除非能给出可执行的风格检查器，否则无客观谓词",
     "改为「通过 ruff / eslint 检查」并接入对应检查器"),
    (re.compile(r"性能更好|更快|优化性能|提升效率"),
     "「更好/更快」无基线也无阈值",
     "给出可测指标，例如「p95 延迟 < 100ms」或「调用次数 ≤ N」"),
]

# 可验证措辞的抽取（LLM 不可用时的降级路径）。只切候选，合法与否交 validate。
_FILE_GLOB_RE = re.compile(
    r"(?:只(?:允许|准)?(?:修改|改|动|编辑)|仅(?:修改|改)|"
    r"only\s+(?:modify|edit|change)|within)\s*[`\"']?([\w./\\*\-]+)")
_MAX_LINES_RE = re.compile(r"(?:不超过|最多|≤|<=|at most|max)\s*(\d+)\s*(?:行|lines?)")
_NO_DEP_RE = re.compile(
    r"(?:不(?:要|准|允许)?(?:新增|添加|引入)|no new|without new)\s*"
    r"(?:第三方|外部|new)?\s*(?:依赖|dependency|dependencies|package)")
_TEST_RE = re.compile(
    r"(?:保证|确保|must pass|保持)\s*[`\"']?([\w./\\]+::\w+|tests?/[\w./]+)[`\"']?\s*(?:通过|pass)?")
_SIGNATURE_RE = re.compile(
    r"[`\"']?([\w.]+)\s*\(\)?\s*(?:的)?\s*(?:函数)?\s*签名(?:不变|稳定|不得改)")
#: call_chain 的措辞比 file_scope 更具体（要求「可达」字样），必须在
#: file_scope 之前判定，否则会被 _FILE_GLOB_RE 抢走。
_CALL_CHAIN_RE = re.compile(
    r"(?:只(?:准|允许)?(?:修改|改)|仅(?:修改|改)|only\s+(?:modify|edit))\s*"
    r"[`\"']?([\w.]+)\s*\(\)?\s*(?:可[达及]|reachable)")
_IMPACT_LIMIT_RE = re.compile(
    r"影响面\s*(?:不超过|最多|≤|<=)\s*(\d+)\s*(?:个)?\s*文件")


def reject_unverifiable(raw: str) -> tuple[str, str] | None:
    """命中不可验证措辞则返回 (reason, suggestion)，否则 None。"""
    for pat, reason, suggestion in _UNVERIFIABLE_HINTS:
        if pat.search(raw or ""):
            return reason, suggestion
    return None


def from_heuristics(items: list[str]) -> list[dict]:
    """把自然语言粗切为候选约束 dict。识别不出的保留 raw_text 待拒绝。"""
    out: list[dict] = []
    for raw in items:
        cand: dict = {"raw_text": raw}
        m = _CALL_CHAIN_RE.search(raw)
        if m:
            cand.update({"type": "call_chain",
                         "scope": {"kind": "graph", "root": m.group(1),
                                   "direction": "callees"}})
        elif (m := _IMPACT_LIMIT_RE.search(raw)):
            cand.update({"type": "impact_limit",
                         "scope": {"kind": "symbols", "symbols": [],
                                   "max_files": int(m.group(1))}})
        elif (m := _SIGNATURE_RE.search(raw)):
            cand.update({"type": "signature_stable",
                         "scope": {"kind": "symbols",
                                   "symbols": [{"name": m.group(1)}]}})
        elif (m := _FILE_GLOB_RE.search(raw)):
            cand.update({"type": "file_scope",
                         "scope": {"kind": "path_glob",
                                   "patterns": [m.group(1).replace("\\", "/").rstrip(".")]}})
        elif (m := _MAX_LINES_RE.search(raw)):
            cand.update({"type": "size_limit",
                         "scope": {"kind": "diff", "max_added": int(m.group(1))}})
        elif _NO_DEP_RE.search(raw):
            cand.update({"type": "dependency_frozen",
                         "scope": {"kind": "manifest",
                                   "files": ["requirements.txt", "pyproject.toml",
                                             "package.json"]}})
        elif (m := _TEST_RE.search(raw)):
            cand.update({"type": "test_preserved",
                         "scope": {"kind": "tests", "selectors": [m.group(1)]}})
        out.append(cand)
    return out
