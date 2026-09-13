# 工具系统基础 — ToolSpec / ToolExecutor / 安全校验 / 注册表
from __future__ import annotations
import asyncio, json, re, time, traceback, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

# 工具以**字符串**返回时的错误前缀。工具约定用 "Error: ..." 表示失败
# （见 apply_patch / shell_command / file_rw 等），而 registry 此前对任何
# 字符串一律记 success=True —— 于是
#     "Error: Could not parse any file changes from the patch."
# 被上层当成「补丁已应用」。Agent 以为改完了、实际文件没动，
# 而 observer 的步骤流转、指标、diff 归因全部跟着错，且**不报错**。
_ERROR_PREFIX_RE = re.compile(
    r"^\s*(error|failed|failure|exception|traceback|patch (rejected|failed)|"
    r"permission denied|command rejected|cannot |could not )\b",
    re.I,
)


def _looks_like_error(text: str) -> bool:
    """字符串返回值是否表示失败。

    保守判定：只认**开头**的错误前缀。不用「包含 error」这种宽判据 ——
    读文件的内容里出现 "error" 是常态，那会把成功误判成失败
    （比漏判更糟：会让 Agent 无谓地重试）。
    """
    return bool(_ERROR_PREFIX_RE.match(text or ""))


# ── 工具定义 ──
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema
    exposure: str = "direct"  # direct | deferred | hidden
    category: str = "general"
    requires_approval: bool = False
    timeout_ms: int = 30000

    def to_openai_function(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict

@dataclass
class ToolCallResult:
    id: str
    name: str
    output: str
    success: bool
    error: str | None = None
    duration_ms: float = 0
    metadata: dict = field(default_factory=dict)

# ── 安全工具 — 路径校验 ──
def safe_resolve_path(target: str, workspace: str) -> Path:
    """防路径穿越：确保目标路径在 workspace 内。

    注意：不能用 ``str.startswith(str(ws))`` 判断——``/data/proj`` 会被
    同级前缀目录 ``/data/proj-evil`` 绕过。必须按路径分量判断。
    """
    ws = Path(workspace).resolve()
    resolved = (ws / target).resolve()
    try:
        inside = resolved == ws or resolved.is_relative_to(ws)
    except AttributeError:  # Python < 3.9 兼容
        inside = resolved == ws or str(resolved).startswith(str(ws) + str(Path(os.sep)))
    if not inside:
        raise PermissionError(f"Path traversal blocked: {target}")
    return resolved

def sanitize_command(command: str) -> str:
    """清洗危险命令 - 阻止已知危险模式"""
    # Use the more comprehensive security module sanitizer
    from backend.security import InputSanitizer
    cleaned, warnings = InputSanitizer.sanitize_shell_command(command)
    if warnings:
        raise PermissionError(f"Command blocked: {'; '.join(warnings)}")
    return cleaned

# ── 输出截断 ──
def truncate_output(output: str, max_chars: int = 16384) -> str:
    if len(output) <= max_chars:
        return output
    half = max_chars // 2
    return output[:half] + f"\n\n[... truncated {len(output) - max_chars} chars ...]\n\n" + output[-half:]

# ── 工具处理器协议 ──
class ToolHandler(Protocol):
    async def __call__(self, arguments: dict, workspace: str) -> Any: ...

# ── 工具注册表 ──
class ToolRegistry:
    """统一工具注册、发现、路由"""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._mcp_tools: dict[str, list[ToolSpec]] = {}  # server_name -> [ToolSpec]
        self._call_history: list[ToolCallResult] = []

    def register(self, spec: ToolSpec, handler: ToolHandler):
        self._tools[spec.name] = spec
        self._handlers[spec.name] = handler

    def unregister(self, name: str):
        self._tools.pop(name, None)
        self._handlers.pop(name, None)

    def register_mcp_server(self, server_name: str, tools: list[ToolSpec]):
        self._mcp_tools[server_name] = tools

    def unregister_mcp_server(self, server_name: str):
        self._mcp_tools.pop(server_name, None)

    def list_tools(self, include_mcp: bool = True, category: str | None = None,
                   exposures: tuple[str, ...] | None = None) -> list[ToolSpec]:
        """列出工具。

        `exposures` 按 `ToolSpec.exposure` 过滤（direct/deferred/hidden）。
        为什么需要它：`exposure` 此前**声明了但全项目无人读取** ——
        于是 29 个工具（含 computer_use / browser_use / spawn_agent 等
        约 30 个不常用工具的完整 JSON Schema）每一轮都发给模型，
        实测 35,130 字符 ≈ 11.7K tokens，占满 24K 会话预算的一轮，
        导致 Agent 只跑 1~3 轮就「预算耗尽」收工。
        """
        tools = list(self._tools.values())
        if include_mcp:
            for server_tools in self._mcp_tools.values():
                tools.extend(server_tools)
        if category:
            tools = [t for t in tools if t.category == category]
        if exposures is not None:
            tools = [t for t in tools if t.exposure in exposures]
        return tools

    def list_tools_openai(self, include_mcp: bool = True,
                          exposures: tuple[str, ...] | None = ("direct",)) -> list[dict]:
        """给模型的原生 tools= 用的 schema 列表。

        默认**只发 direct**：deferred/hidden 的工具不占每轮上下文。
        `deferred` 的语义是「需要时再给」——目前由 `_tools_digest` 在提示词里
        列出名字让模型知道它们存在（完整 schema 暂不注入，见 list_tools 的说明）。
        """
        return [t.to_openai_function()
                for t in self.list_tools(include_mcp, exposures=exposures)]

    def get_tool(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    async def execute(self, name: str, arguments: dict, workspace: str = ".") -> ToolCallResult:
        """执行工具并返回结果"""
        start = time.time()
        handler = self._handlers.get(name)

        if not handler:
            # 尝试 MCP 工具
            for server_name, tools in self._mcp_tools.items():
                for t in tools:
                    if t.name == name:
                        return await self._execute_mcp(server_name, name, arguments)

            result = ToolCallResult(id="", name=name, output="", success=False,
                                     error=f"Unknown tool: {name}", duration_ms=0)
        else:
            try:
                output = await handler(arguments, workspace)
                duration = (time.time() - start) * 1000
                if isinstance(output, dict) and "success" in output:
                    success = bool(output.get("success"))
                    output_text = output.get("output")
                    if output_text is None:
                        output_text = output.get("stdout", "")
                    error_text = output.get("error") or output.get("stderr") or None
                    output_str = str(output_text or "")
                    truncated = len(output_str) > 65536
                    metadata = {k: v for k, v in output.items() if k not in {"success", "output", "stdout", "error", "stderr"}}
                    metadata["truncated"] = truncated
                    result = ToolCallResult(
                        id="", name=name,
                        output=truncate_output(output_str) if truncated else output_str,
                        success=success,
                        error=str(error_text) if error_text else None,
                        duration_ms=duration,
                        metadata=metadata,
                    )
                else:
                    output_str = str(output)
                    truncated = len(output_str) > 65536
                    is_err = _looks_like_error(output_str)
                    result = ToolCallResult(
                        id="", name=name,
                        output=truncate_output(output_str) if truncated else output_str,
                        # 字符串返回值必须**判别**是否错误，不能一律当成功 ——
                        # 见 _looks_like_error 的说明。
                        success=not is_err,
                        error=output_str[:500] if is_err else None,
                        duration_ms=duration,
                        metadata={"truncated": truncated}
                    )
            except PermissionError as e:
                result = ToolCallResult(id="", name=name, output="", success=False,
                                         error=f"Permission denied: {e}", duration_ms=(time.time()-start)*1000)
            except asyncio.TimeoutError:
                spec = self._tools.get(name)
                timeout = spec.timeout_ms / 1000 if spec else 30
                result = ToolCallResult(id="", name=name, output="", success=False,
                                         error=f"Timeout after {timeout}s", duration_ms=(time.time()-start)*1000)
            except Exception as e:
                result = ToolCallResult(id="", name=name, output="", success=False,
                                         error=f"{type(e).__name__}: {str(e)[:500]}",
                                         duration_ms=(time.time()-start)*1000)

        self._call_history.append(result)
        if len(self._call_history) > 100:
            self._call_history = self._call_history[-100:]
        return result

    async def _execute_mcp(self, server_name: str, tool_name: str, arguments: dict) -> ToolCallResult:
        return ToolCallResult(id="", name=tool_name, output="", success=False,
                             error=f"MCP server '{server_name}' not connected")

    def stats(self) -> dict:
        recent = self._call_history[-20:]
        success_rate = sum(1 for r in recent if r.success) / max(len(recent), 1)
        return {
            "registered_tools": len(self._tools),
            "mcp_servers": len(self._mcp_tools),
            "total_calls": len(self._call_history),
            "recent_success_rate": f"{success_rate:.0%}",
        }


tool_registry = ToolRegistry()