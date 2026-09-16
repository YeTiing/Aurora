"""澄清问题生成 —— 规范 §5.5，**A3 的核心价值**。

## 为什么这是核心

规范把「不确定性感知执行」的价值定位在提问质量上，而不是风险打分上：

    不要问：「是否继续？」
    要问：  能消除歧义的问题。

它给的正例：

    「当前可以**保留旧接口并增加适配层**，也可以**修改所有调用方**。
      是否需要保持向后兼容？」

这个问题之所以好，是因为它**给出了两个具体选项**，用户只需选一个，
而且每个选项的后果都说清楚了。相比之下「是否继续」没有信息量 ——
用户只能回答「是」，那等于没问。

## 禁止清单（规范 §5.5 明文）

    ❌ 开放式问题
    ❌ 笼统问题
    ❌ 超过 3 个选项

三者都用代码约束：生成器只产出「A 还是 B」句式、选项上限 3、
且**校验器会在返回前拒绝不合规的问题**（宁可退化为 `plan_first`
也不问一个无效问题 —— 无效提问会训练用户无脑同意，规范 §5.1）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 每个风险信号对应的「两个可行处理方式」。
# 这是本模块的**知识**：什么信号该问什么。做成数据便于审查与扩充。
#
# 每项形如 (问题模板, 选项A, 选项B)，模板里 {a}/{b} 会被选项文字替换。
_QUESTION_BANK: dict[str, tuple[str, str, str]] = {
    "requirement_ambiguous": (
        "需求有多种合理解释。可以{a}，也可以{b}。你希望哪一种？",
        "按最小范围实现（只改必要处）",
        "按完整范围实现（含相关调用方与测试）",
    ),
    "touches_public_api": (
        "这次改动会触及公共接口。可以{a}，也可以{b}。是否需要保持向后兼容？",
        "保留旧接口并增加适配层",
        "直接修改所有调用方",
    ),
    "target_symbol_unique": (
        "无法唯一定位目标符号（有多个同名候选）。可以{a}，也可以{b}。",
        "按文件/行号指定具体那一个",
        "改全部同名符号",
    ),
    "callgraph_fresh": (
        "调用图已过期，影响面可能不完整。可以{a}，也可以{b}。",
        "先重建索引再继续（更慢但影响面准确）",
        "按现有信息继续（更快但可能漏掉调用方）",
    ),
    "has_related_tests": (
        "没有找到与该改动关联的测试。可以{a}，也可以{b}。",
        "先补一个覆盖该行为的测试再改",
        "直接改，改完人工验证",
    ),
    "involves_risky_domain": (
        "这次改动涉及高风险领域（数据库/权限/安全/依赖）。可以{a}，也可以{b}。",
        "先在计划里列出回滚方案再执行",
        "直接执行，出问题再回滚",
    ),
    "scope_over_budget": (
        "预计改动范围超出常规预算。可以{a}，也可以{b}。",
        "先只做最小必要改动",
        "按完整方案一次改完",
    ),
    "rollback_costly": (
        "这次改动回滚成本较高。可以{a}，也可以{b}。",
        "分小步提交，每步可独立回滚",
        "一次性完成",
    ),
}

# 选项上限（规范 §5.5 明文：超过 3 个选项禁止）
MAX_OPTIONS = 3

# 被禁止的笼统问法 —— 出现即判问题不合规
_BANNED_PATTERNS = (
    r"是否继续", r"要不要继续", r"可以吗", r"行不行", r"continue\?",
    r"^\s*(确定|确认)\s*[?？]?\s*$", r"有没有问题",
)


@dataclass
class Question:
    """一个澄清问题（规范 §5.5 的形态）。"""

    signal: str
    text: str
    options: list[str]

    def is_valid(self) -> bool:
        return not validate(self)

    def to_dict(self) -> dict:
        return {"signal": self.signal, "text": self.text, "options": self.options}


def validate(q: Question) -> list[str]:
    """校验问题是否符合规范 §5.5。返回问题列表（空 = 合规）。

    独立成公开函数（而不是只在生成器内部用）：调用方与测试都需要
    对**外部传入**的问题做同样的校验 —— 否则「禁止开放式问题」只约束了
    我们自己的生成器，别人拼一个问题塞进来就绕过了。
    """
    problems: list[str] = []
    text = (q.text or "").strip()

    if not text:
        return ["问题为空"]
    if len(q.options) < 2:
        problems.append(
            f"只有 {len(q.options)} 个选项 —— 规范要求给出「A 还是 B」两个具体选项")
    if len(q.options) > MAX_OPTIONS:
        problems.append(f"{len(q.options)} 个选项超过上限 {MAX_OPTIONS}")
    for pat in _BANNED_PATTERNS:
        if re.search(pat, text, re.I):
            problems.append(f"问法过于笼统（匹配 {pat!r}）—— 规范禁止开放式提问")
            break
    # 「A 还是 B」句式：至少要出现一次**提出选择**的连词。
    #
    # ⚠️ 这里必须覆盖「可以 A，也可以 B」这种并列式 —— 实测踩过：
    # 只认「还是/或者」会把自己生成的合规问题全部误杀（模板用的正是
    # 「可以{a}，也可以{b}」）。而误杀的后果不是报错，是**静默丢弃问题**：
    # 用户看不到任何提问，也看不到原因（只有一条 warning 日志）。
    if not _ASK_ALTERNATIVE_RE.search(text):
        problems.append("不是「给出两个选项」的句式 —— 用户无法用选项回答")
    return problems


# 提出选择的句式。三种真实写法都要认：
#   还是 / 或者 / 哪一  —— 疑问式（「A 还是 B？」）
#   可以 A，也可以 B      —— 并列式（规范 §5.5 的原例就是这种）
#   or / vs               —— 英文
_ASK_ALTERNATIVE_RE = re.compile(
    r"还是|或者|哪一|也可以|既可以|要么|\bor\b|vs\.?", re.I)


def generate(signals, *, scores_reasons=None, max_questions: int = 2) -> list[Question]:
    """按触发的信号生成澄清问题。

    `max_questions` 默认 2：规范 §5.1 明确说「全问 → 用户被淹没」。
    一次任务问超过 2 个问题就很难得到认真回答。
    """
    out: list[Question] = []
    for name, (tpl, a, b) in _QUESTION_BANK.items():
        if len(out) >= max_questions:
            break
        if not _should_ask(signals, name):
            continue
        q = Question(signal=name, text=tpl.format(a=a, b=b), options=[a, b])
        problems = validate(q)
        if problems:
            # 生成器自身产出了不合规问题 —— 那是**我们的 bug**，
            # 记下来但不问（宁可少问一个，也不问一个无效问题）。
            import logging
            logging.getLogger("necessity.autonomy.clarify").warning(
                "生成的问题不合规，已丢弃: %s | %s", problems, q.text)
            continue
        out.append(q)
    return out


def _should_ask(signals, name: str) -> bool:
    """该信号是否值得问一句。

    未检测（None）**不问** —— 问「我不知道有没有风险，你想怎样」
    对用户毫无帮助。未检测应该体现在风险分与 `stop` 分支上
    （见 `score.py`），而不是变成一个无从回答的问题。
    """
    v = getattr(signals, name, None)
    if v is None:
        return False
    if name in ("target_symbol_unique", "callgraph_fresh", "has_related_tests"):
        return v is False
    return v is True


__all__ = ["MAX_OPTIONS", "Question", "generate", "validate"]
