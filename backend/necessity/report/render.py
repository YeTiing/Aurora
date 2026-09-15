"""证据包的人可读渲染 —— 规范 §3.7。

## 首屏三条（规范硬性要求）

    「`report.md`（人可读，**首屏**放「结论 + 未验证项 + 完整性状态」）」

顺序不能改。理由：`status=partial` 若不显眼，用户会把一份**缺必要性证据**
的报告当成完整的（§3.6 硬性要求 1：「必须在显眼位置标注」）。
所以完整性状态放在最前面，而不是文末的小字注脚。

## 渲染纪律

不美化、不省略、不用「等」字掩盖数量。「未验证 3 项」就逐个列出 ——
报告的价值恰恰在于用户能一眼看到**问题**，而不是看到「看起来完成了」。
"""
from __future__ import annotations

from .schema import EvidenceBundle

_STATUS_LABEL = {
    "complete": "✅ 完整（含必要性证据）",
    "partial": "⚠️ **不完整** —— 必要性证据尚未生成",
}


def render_markdown(b: EvidenceBundle) -> str:
    """把证据包渲染成 Markdown。首屏 = 结论 + 未验证项 + 完整性状态。"""
    out: list[str] = []
    out.append(f"# 证据化补丁报告 — `{b.task_id or '(unknown)'}`")
    out.append("")

    # ── 首屏①：完整性状态（必须最显眼）───────────────────────────
    # ⚠️ 顺序是规范硬要求（§3.7）：首屏 = 结论 + 未验证项 + 完整性状态。
    # 三条都必须落在**首屏**内 —— 采集失败项多的时候不能把它们挤出去，
    # 所以失败明细放在「结论」**之后**（它仍属首屏次段，但不是前排）。
    out.append(f"## 完整性：{_STATUS_LABEL.get(b.status, b.status)}")
    if b.pending:
        out.append("")
        out.append(f"待补项：{'、'.join(b.pending)}")
        out.append("")
        out.append("> 本报告的**必要性判定**尚未生成。在那之前，"
                   "「每处改动都必要」这一点**没有证据**。")
    out.append("")

    # ── 首屏②：结论 ──────────────────────────────────────────────
    out.append("## 结论")
    out.append("")
    out.append(f"- 改动：{len(b.changes)} 个文件 / "
               f"+{sum(c.added for c in b.changes)}-{sum(c.removed for c in b.changes)}")
    out.append(f"- 影响面：{len(b.impact)} 个使用点")
    out.append(f"- 验证：{sum(1 for v in b.verification if v.passed)}/"
               f"{len(b.verification)} 项通过")
    out.append(f"- 安全：{_security_line(b)}")
    if b.requirement_coverage:
        covered = sum(1 for c in b.requirement_coverage if c.covered)
        out.append(f"- 需求覆盖：{covered}/{len(b.requirement_coverage)}")
    else:
        out.append("- 需求覆盖：**未采集**（无法判断需求是否被覆盖）")
    out.append("")

    # ── 首屏③：未验证项（规范标为「核心」）──────────────────────
    out.append("## 未验证项")
    out.append("")
    if b.unverified:
        for item in b.unverified:
            out.append(f"- {item}")
    else:
        # 理论上到不了这里（schema 有兜底），但留一条以防上游绕过
        out.append("- ⚠️ 未采集到未验证项 —— 这本身可疑，请人工确认")
    out.append("")

    # 采集失败明细：仍在首屏区域，但排在三条硬要求之后
    if b.collection_errors:
        out.append("## 采集失败项（这些字段是「未采集」，不是「无」）")
        out.append("")
        for err in b.collection_errors:
            out.append(f"- {err}")
        out.append("")

    # ── 次要内容 ────────────────────────────────────────────────
    if b.staleness.note:
        out.append(f"> ⚠️ {b.staleness.note}")
        out.append("")

    if b.residual_risk:
        out.append("## 剩余风险（需人工判断）")
        out.append("")
        for r in b.residual_risk:
            out.append(f"- {r}")
        out.append("")

    if b.changes:
        out.append("## 改动明细")
        out.append("")
        out.append("| 文件 | 类型 | +/- | 来源 |")
        out.append("|---|---|---|---|")
        for c in b.changes:
            out.append(f"| `{c.path}` | {c.kind} | +{c.added}-{c.removed} | "
                       f"{'Agent' if c.by_agent else '外部'} |")
        out.append("")

    if b.impact:
        out.append("## 影响面")
        out.append("")
        out.append("| 符号 | 位置 | 备注 |")
        out.append("|---|---|---|")
        for i in b.impact:
            mark = " ⚠️ 可能过期" if i.possibly_stale else ""
            out.append(f"| `{i.symbol}` | `{i.path}:{i.line}` | {i.note}{mark} |")
        out.append("")

    if b.necessity:
        out.append("## 必要性")
        out.append("")
        out.append("| 改动 | 文件 | 结论 | 依据 |")
        out.append("|---|---|---|---|")
        for n in b.necessity:
            out.append(f"| `{n.hunk_id}` | `{n.path}` | "
                       f"{'必要' if n.necessary else '冗余'} | {n.reason} |")
        out.append("")

    if b.verification:
        out.append("## 验证")
        out.append("")
        for v in b.verification:
            mark = "✅" if v.passed else "❌"
            out.append(f"- {mark} `{v.command}` (exit={v.exit_code})")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _security_line(b: EvidenceBundle) -> str:
    s = b.security
    if not s.scanned:
        # 「没扫」与「扫了没发现」必须区分 —— 见 SecurityEvidence 的注释
        return f"**未扫描**（{s.note or '原因未记录'}）"
    parts = [f"{k}={v}" for k, v in
             (("critical", s.critical), ("high", s.high),
              ("medium", s.medium), ("low", s.low)) if v]
    return "已扫描，" + ("、".join(parts) if parts else "无发现")


def write_report(b: EvidenceBundle, out_dir: str) -> tuple[str, str]:
    """落盘 `.necessity/bundles/{task_id}.json` + `.md`（规范 §3.7）。

    返回 (json_path, md_path)。JSON 是机器可读的**唯一真相**，
    Markdown 只是它的一个视图 —— 所以两者从同一个对象渲染，
    不会出现「报告说 A、数据是 B」。
    """
    import json
    from pathlib import Path

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    tid = b.task_id or "unknown"
    jp = d / f"{tid}.json"
    mp = d / f"{tid}.md"
    jp.write_text(json.dumps(b.to_dict(), ensure_ascii=False, indent=2,
                             default=str), encoding="utf-8")
    mp.write_text(render_markdown(b), encoding="utf-8")
    return str(jp), str(mp)


__all__ = ["render_markdown", "write_report"]
