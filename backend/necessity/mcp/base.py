"""MCP 工具的共享类型与参数校验。

为什么手写校验而不引入 `jsonschema`：
    这是项目「最小依赖」原则的落点，且 MCP 工具的参数面很窄（4 个工具、
    十来个字段）。手写校验还能给出**面向 Agent 的**错误消息 —— 参数错时
    消息会直接进入模型上下文，pyright 式的「'x' is not of type 'string'」
    不如「参数 'direction' 只能是 'necessary' 或 'culprit'」有用。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__all__ = [
    "ToolError",
    "ToolOutcome",
    "Tool",
    "require",
    "optional",
    "str_list",
    "read_diff_arg",
    "obj_schema",
]


class ToolError(Exception):
    """工具层错误。`code` 默认 -32602（参数不合法），也可标 -32000（工具内部）。"""

    def __init__(self, message: str, code: int = -32602):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class ToolOutcome:
    """工具成功时的产出：人读文本 + 可选的机器可读结构。"""
    text: str
    structured: dict | None = None


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[[dict], ToolOutcome]
    output_schema: dict | None = None
    annotations: dict = field(default_factory=dict)


def require(params: dict, key: str, typ: type) -> Any:
    if key not in params or params[key] is None:
        raise ToolError(f"缺少必需参数 '{key}'（类型 {typ.__name__}）")
    return _check(params[key], key, typ)


def optional(params: dict, key: str, typ: type, default: Any = None) -> Any:
    if key not in params or params[key] is None:
        return default
    return _check(params[key], key, typ)


def _check(val: Any, key: str, typ: type) -> Any:
    # bool 是 int 的子类：JSON 里 true 不该被当成 1 接受（否则 max_test_runs=true 静默通过）
    if typ is int and isinstance(val, bool):
        raise ToolError(f"参数 '{key}' 必须是 {typ.__name__}，收到 bool")
    if typ is str and not isinstance(val, str):
        raise ToolError(
            f"参数 '{key}' 类型错误：期望 string，收到 {type(val).__name__}")
    if typ is int and not isinstance(val, int):
        raise ToolError(
            f"参数 '{key}' 类型错误：期望 integer，收到 {type(val).__name__}")
    if typ is dict and not isinstance(val, dict):
        raise ToolError(
            f"参数 '{key}' 类型错误：期望 object，收到 {type(val).__name__}")
    return val


def str_list(params: dict, key: str) -> list[str]:
    if key not in params or params[key] is None:
        return []
    val = params[key]
    if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
        raise ToolError(f"参数 '{key}' 必须是字符串数组")
    return list(val)


def read_diff_arg(params: dict) -> str:
    """diff 既可直接给文本，也可给文件路径（二选一）。

    给路径是实用考虑：真实 diff 经常很长，塞进工具参数既费 token 又容易
    转义出错。二者都支持，但必须显式二选一，避免「两个都给」的歧义。
    """
    inline = params.get("diff")
    path = params.get("diff_path")
    if inline is None and path is None:
        raise ToolError("需要 'diff'（unified diff 文本）或 'diff_path'（文件路径）之一")
    if path is not None:
        if not isinstance(path, str):
            raise ToolError("'diff_path' 必须是字符串")
        p = Path(path)
        if not p.is_file():
            raise ToolError(f"diff_path 不是文件: {path}")
        return p.read_text(encoding="utf-8", errors="replace")
    if not isinstance(inline, str):
        raise ToolError("'diff' 必须是字符串")
    return inline


def obj_schema(props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
