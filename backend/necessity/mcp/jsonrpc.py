"""JSON-RPC 2.0 分帧 —— 手写 stdio 传输层，不引入 `mcp` / `fastmcp`。

为什么手写（INTEGRATION.md §1.3 的「能用标准库就用标准库」+ 项目最小依赖）：

  * MCP 的 stdio 传输只是「JSON-RPC 2.0 + 一个分帧约定」，没有需要第三方
    库才能处理的复杂度；引入依赖反而把「能力层」绑死在某个 SDK 版本上。
  * 分帧逻辑必须**可测**。把它与调度分开后，一个假 stream 就能跑往返，
    不需要起子进程、不需要真 MCP client（测试正是这么做的）。

分帧同时支持两种约定，因为二者在现实里都存在，且切换成本极低：

  1. `Content-Length` 帧（与 `core/index/rpc.py` 同款，LSP 风格）—— 本层主选。
  2. 换行分隔 JSON —— MCP 官方 stdio 传输实际使用的约定。

读取时自动识别，回复时**沿用请求的约定**（`FrameReader.mode`），这样无论
宿主用哪种，回复都能被正确解析 —— 不会出现「服务端发 Content-Length、
客户端按行读」这种静默失配。

`Content-Length` 必须按 **UTF-8 字节数**算，不是字符数：diff / 证据里几乎
必然出现中文，按字符数算会让对端截断或多读，表现为随机 read error。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "JsonRpcError",
    "ParseError",
    "FrameReader",
    "encode_message",
    "encode_line",
    "success_response",
    "error_response",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
]

# JSON-RPC 2.0 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# 帧约定标记
MODE_HEADER = "content-length"
MODE_LINE = "line"


class JsonRpcError(Exception):
    """可被调度层转成 JSON-RPC error 响应的异常（工具内部也可抛它）。"""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ParseError(Exception):
    """帧/JSON 解析失败。区别于 JsonRpcError：它没有可用的请求 id。"""


def encode_message(data: dict) -> bytes:
    """编码为 Content-Length 帧（UTF-8 字节长度）。"""
    body = json.dumps(data, default=str, ensure_ascii=False)
    header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
    return (header + body).encode("utf-8")


def encode_line(data: dict) -> bytes:
    """编码为换行分隔 JSON（MCP 官方 stdio 约定）。"""
    body = json.dumps(data, default=str, ensure_ascii=False)
    return (body + "\n").encode("utf-8")


def success_response(mid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def error_response(mid: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": int(code), "message": str(message)}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}


class FrameReader:
    """从一个 `read(n)` 风格的二进制流里读出 JSON-RPC 消息。

    自持缓冲区是**分帧正确性的关键**：网络/管道的一次 `read` 不保证返回
    一条完整消息（经典 bug：请求被拆成两次读取，第一次收到半个 body 就
    开始 `json.loads`）。这里只在缓冲区里出现完整 `Content-Length` 字节后
    才解析，天然容忍任意切分。
    """

    def __init__(self, stream: Any, chunk_size: int = 65536):
        self._stream = stream
        self.chunk_size = max(1, int(chunk_size))
        self._buf = bytearray()
        self._eof = False
        # 最近一条消息的帧约定，供回复沿用；默认 Content-Length
        self.mode = MODE_HEADER

    # ── 缓冲填充 ────────────────────────────────────────────────

    def _fill(self) -> bool:
        if self._eof:
            return False
        # read1 在管道上「有多少给多少」，不会像 read(n) 那样等满 n 字节；
        # 这是 stdio 上不阻塞的关键（read(65536) 会一直等到 EOF 才返回）。
        reader = getattr(self._stream, "read1", None) or self._stream.read
        try:
            chunk = reader(self.chunk_size)
        except TypeError:
            chunk = reader()
        if not chunk:
            self._eof = True
            return False
        self._buf.extend(chunk)
        return True

    def _read_line(self) -> bytes | None:
        """读一行（含换行符）。EOF 且缓冲为空 → None；否则返回残余。"""
        while True:
            i = self._buf.find(b"\n")
            if i >= 0:
                line = bytes(self._buf[: i + 1])
                del self._buf[: i + 1]
                return line
            if not self._fill():
                if self._buf:
                    line = bytes(self._buf)
                    self._buf.clear()
                    return line
                return None

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            if not self._fill():
                raise ParseError(
                    f"body 不完整：期望 {n} 字节，只收到 {len(self._buf)} 字节"
                )
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    # ── 主入口 ──────────────────────────────────────────────────

    def read_message(self) -> dict | None:
        """读一条消息。None 表示干净的 EOF（流结束）。解析失败抛 ParseError。"""
        text = ""
        while True:
            line = self._read_line()
            if line is None:
                return None
            text = line.decode("utf-8", "replace").strip()
            if text:
                break

        if text.lower().startswith("content-length:"):
            self.mode = MODE_HEADER
            raw_len = text.split(":", 1)[1].strip()
            try:
                length = int(raw_len or 0)
            except ValueError:
                raise ParseError(f"非法 Content-Length: {raw_len!r}") from None
            if length <= 0:
                raise ParseError("Content-Length 必须为正整数")
            # 吃掉剩余 header，直到空行
            while True:
                h = self._read_line()
                if h is None:
                    raise ParseError("header 段在空行前就 EOF 了")
                if not h.strip():
                    break
            return self._loads(self._read_exact(length))

        # 换行分隔约定：这一行本身就是完整 JSON
        self.mode = MODE_LINE
        return self._loads(text.encode("utf-8"))

    @staticmethod
    def _loads(raw: bytes) -> dict:
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ParseError(f"JSON 解析失败: {e}") from None
        if not isinstance(msg, dict):
            raise ParseError("JSON-RPC 消息必须是对象")
        return msg
