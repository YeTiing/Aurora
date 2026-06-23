# request_user_input — Codex同款用户交互工具
from __future__ import annotations
import json
from typing import Any
from .base import ToolSpec, ToolCallResult

REQUEST_USER_INPUT_SPEC = ToolSpec(
    name="request_user_input",
    description="Request user input for one to three short questions and wait for the response. Use when you need clarification or the user's decision to continue.",
    parameters={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "Questions to show the user. Prefer 1 and do not exceed 3.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Stable identifier for mapping answers (snake_case)."},
                        "header": {"type": "string", "description": "Short header label (12 or fewer chars)."},
                        "question": {"type": "string", "description": "Single-sentence prompt to show the user."},
                        "options": {
                            "type": "array",
                            "description": "Provide 2-3 mutually exclusive choices.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string", "description": "User-facing label (1-5 words)."},
                                    "description": {"type": "string", "description": "One short sentence explaining impact/tradeoff."},
                                },
                                "required": ["label", "description"]
                            }
                        }
                    },
                    "required": ["id", "header", "question", "options"]
                }
            }
        },
        "required": ["questions"]
    },
    category="interaction",
    exposure="direct",
)

# 最近一次用户输入的存储
_pending_inputs: dict[str, Any] = {}


async def request_user_input_handler(arguments: dict, workspace: str = ".") -> ToolCallResult:
    """请求用户输入。在CLI模式下显示为交互式提问；在API模式下通过SSE推送"""
    questions = arguments.get("questions", [])
    if not questions:
        return ToolCallResult(id="", name="request_user_input", output="", success=False, error="No questions provided")
    if len(questions) > 3:
        questions = questions[:3]

    # 格式化提问
    output_parts = ["## Questions for you:\n"]
    for i, q in enumerate(questions, 1):
        output_parts.append(f"**{i}. {q.get('question', '')}**")
        output_parts.append(f"  Header: {q.get('header', '')}")
        options = q.get("options", [])
        if options:
            for j, opt in enumerate(options):
                output_parts.append(f"  {chr(97+j)}) {opt.get('label', '')} — {opt.get('description', '')}")

    output = "\n".join(output_parts)

    # 存储待处理的输入请求
    request_id = f"req_{hash(json.dumps(questions, sort_keys=True))}"
    _pending_inputs[request_id] = {
        "questions": questions,
        "workspace": workspace,
        "timestamp": __import__('time').time(),
    }

    return ToolCallResult(
        id="", name="request_user_input", output=output, success=True,
        metadata={"request_id": request_id, "question_count": len(questions)}
    )


def get_pending_requests() -> dict:
    """获取所有待处理的用户输入请求"""
    return dict(_pending_inputs)


def resolve_request(request_id: str, answers: list[str]) -> dict | None:
    """解析用户的回答"""
    if request_id not in _pending_inputs:
        return None
    request = _pending_inputs.pop(request_id)
    result = {}
    for i, q in enumerate(request["questions"]):
        qid = q.get("id", f"q{i}")
        result[qid] = answers[i] if i < len(answers) else ""
    return result