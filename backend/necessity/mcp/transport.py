"""stdio 传输层 + 命令行入口 —— 把 McpServer 接到真实流上的一层薄壳。

与 server.py 分工：server.py 只管「一条消息 → 一条响应」的协议语义；
本模块管「怎么在流上收发字节、怎么优雅退出」。分开的理由是可测性 ——
测试直接调 `McpServer.handle_message`，只有一条集成测试需要本模块。

分帧沿用请求的约定回复（Content-Length ↔ 换行分隔），见 jsonrpc.FrameReader.mode。
"""

from __future__ import annotations

import sys
from typing import Any

from .jsonrpc import FrameReader, encode_line, encode_message, error_response
from .registry import build_registry, guard_boundary_error, GUARD_TOOL_NAMES
from .server import McpServer

__all__ = ["run_stdio", "main"]


def _write(stdout, mode: str, msg: dict) -> None:
    payload = encode_message(msg) if mode == "content-length" else encode_line(msg)
    stdout.write(payload)
    stdout.flush()


def run_stdio(stdin: Any = None, stdout: Any = None,
              server: McpServer | None = None) -> int:
    """把服务端接到 stdio，返回进程退出码。

    优雅停止的三种来源：宿主发 `exit`、EOF（管道关闭）、KeyboardInterrupt。
    三者都返回 0 且不抛异常 —— MCP 进程异常退出会直接断掉宿主会话。

    一条坏消息（JSON 解析失败 / 帧损坏）只回一条 parse error，然后**继续**：
    单个坏帧不该终止整场会话。
    """
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    srv = server or McpServer()
    reader = FrameReader(stdin)

    while True:
        try:
            msg = reader.read_message()
        except Exception as e:
            srv._log(f"帧解析失败: {e}")
            _write(stdout, reader.mode,
                   error_response(None, -32700, f"Parse error: {e}"))
            continue
        if msg is None:
            srv._log("stdin EOF，退出")
            break
        try:
            resp = srv.handle_message(msg)
            if resp is not None:
                _write(stdout, reader.mode, resp)
            if srv.should_exit:
                srv._log("收到 exit，退出")
                break
        except KeyboardInterrupt:
            break
        except Exception as e:   # 最后的兜底：任何意外都不断连接
            srv._log(f"处理消息时未预期异常: {type(e).__name__}: {e}")
            _write(stdout, reader.mode,
                   error_response(msg.get("id"), -32603, f"内部错误: {e}"))
    return 0


def _select_tools(spec: str) -> dict | None:
    """解析 `--tools a,b` 白名单。空 -> None（全部）。"""
    if not spec:
        return None
    registry = build_registry()
    out = {}
    for name in (s.strip() for s in spec.split(",")):
        if not name:
            continue
        if name in GUARD_TOOL_NAMES:
            print(guard_boundary_error(name), file=sys.stderr)
            raise SystemExit(2)
        if name not in registry:
            print(f"未知工具: {name}", file=sys.stderr)
            raise SystemExit(2)
        out[name] = registry[name]
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="necessity-mcp",
        description="Necessity MCP server（Reduce / Attribution / Context）")
    p.add_argument("--tools", default="",
                   help="逗号分隔的工具白名单，缺省全部；Guard 无法通过 MCP 暴露")
    kwargs = vars(p.parse_args(argv))
    return run_stdio(server=McpServer(tools=_select_tools(kwargs["tools"])))


if __name__ == "__main__":
    raise SystemExit(main())
