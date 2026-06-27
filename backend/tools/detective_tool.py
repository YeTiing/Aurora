"""Detective tool — trace bugs to their root cause commit."""
import json
from backend.tools.base import ToolSpec, ToolCallResult
import logging
logger = logging.getLogger("aurora")

async def detective_handler(action: str = "", file: str = "", lines: str = "", bug: str = "") -> ToolCallResult:
    try:
        from backend.diff_detective import get_detective
        d = get_detective()

        if action == "analyze":
            if not file:
                return ToolCallResult(success=False, output="", error="'file' required")
            line_nums = None
            if lines:
                try: line_nums = [int(x) for x in lines.split(",") if x.strip().isdigit()]
                except Exception: logger.debug('detective tool import failed', exc_info=True)
            desc = bug or "Bug investigation"
            result = await d.trace_bug_origin(file, desc, line_nums)
            return ToolCallResult(success=True, output=json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "blame":
            if not file:
                return ToolCallResult(success=False, output="", error="'file' required")
            line_nums = None
            if lines:
                try: line_nums = [int(x) for x in lines.split(",") if x.strip().isdigit()]
                except Exception: logger.debug('detective tool exec failed', exc_info=True)
            report = await d.analyze_file(file, line_nums)
            result = {
                "file": file,
                "suspicious_lines": [{"line": bl.line_no, "content": bl.content[:120], "commit": bl.commit_short, "author": bl.author, "date": bl.date} for bl in report.suspicious_lines[:20]],
                "suspect_commits": [{"hash": c.short_hash, "message": c.message[:150]} for c in report.suspect_commits[:5]],
                "hypothesis": report.root_cause_hypothesis,
            }
            return ToolCallResult(success=True, output=json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "bisect":
            bad = bug or "HEAD"
            good = lines or "HEAD~10"
            test = file or "python -m pytest -x"
            result = await d.bisect(bad, good, test)
            return ToolCallResult(success=True, output=json.dumps(result, indent=2, ensure_ascii=False))

        else:
            return ToolCallResult(success=False, output="", error=f"Unknown action: {action}")

    except Exception as e:
        return ToolCallResult(success=False, output="", error=f"{type(e).__name__}: {str(e)[:300]}")

DETECTIVE_SPEC = ToolSpec(
    name="detective",
    description="Bug forensics: trace bugs to root cause commit. Actions: analyze (full trace), blame (line-by-line commit history), bisect (automated git bisect).",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["analyze", "blame", "bisect"]},
            "file": {"type": "string", "description": "File path to investigate"},
            "lines": {"type": "string", "description": "Line numbers (comma-separated) or commit range for bisect"},
            "bug": {"type": "string", "description": "Bug description or bad commit hash for bisect"},
        },
        "required": ["action"],
    },
)
