"""MCP server 测试（二）：分帧 + stdio 传输 + 真进程往返。

分帧是最容易出「静默错」的一层：错一个字节长度或不允许跨 read 切分，
表现为随机 read error / 请求丢失，而单条大消息的测试往往发现不了。
所以这里专门覆盖 UTF-8 长度、跨读重组、一读多帧、坏帧存活、EOF。
"""
import io
import json
import os
import subprocess
import sys

import pytest

from tests._necessity_mcp_helpers import (
    EXPECTED_TOOLS,
    ChunkedStream,
    McpServer,
    ROOT,
    call,
    content_text,
    decode_lines,
    make_tiny_git_repo,
)
from backend.necessity.mcp.jsonrpc import FrameReader, encode_line, encode_message
from backend.necessity.mcp.transport import run_stdio


# ══ 分帧 ═══════════════════════════════════════════════════════

def test_framing_reassembles_request_split_across_reads():
    """经典 bug：请求被拆成两次读取，第一次只有半个 body。"""
    msg = {"jsonrpc": "2.0", "id": 42, "method": "tools/list", "params": {}}
    raw = encode_message(msg)
    reader = FrameReader(ChunkedStream(raw, [len(raw) // 2, 5, 1, 3]))
    assert reader.read_message() == msg
    assert reader.mode == "content-length"


def test_framing_split_inside_header_is_tolerated():
    """切分点落在 header 中间（甚至冒号前后）也必须重组成功。"""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    raw = encode_message(msg)
    for cut in (3, 10, 15, 19):
        reader = FrameReader(ChunkedStream(raw, [cut, 2, 1]))
        assert reader.read_message() == msg, f"cut={cut}"


def test_framing_utf8_length_not_char_count():
    """Content-Length 必须按 UTF-8 字节数 —— 中文请求是最容易踩的坑。"""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "notifications/initialized",
           "params": {"note": "中文注释会产生多字节"}}
    raw = encode_message(msg)
    header, body = raw.split(b"\r\n\r\n", 1)
    declared = int(header.split(b":")[1])
    assert declared == len(body)
    assert declared != len(json.dumps(msg, ensure_ascii=False))
    assert FrameReader(io.BytesIO(raw)).read_message() == msg


def test_framing_line_delimited_mode():
    msg = {"jsonrpc": "2.0", "id": 5, "method": "ping"}
    reader = FrameReader(io.BytesIO(encode_line(msg)))
    assert reader.read_message() == msg
    assert reader.mode == "line"


def test_two_messages_in_one_read_are_both_returned():
    a = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    b = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    reader = FrameReader(io.BytesIO(encode_message(a) + encode_message(b)))
    assert reader.read_message() == a
    assert reader.read_message() == b
    assert reader.read_message() is None          # EOF


def test_framing_eof_mid_body_does_not_hang():
    """body 声称 999 字节但流提前结束 -> 抛错而不是死等。"""
    raw = b"Content-Length: 999\r\n\r\nshort"
    with pytest.raises(Exception):
        FrameReader(io.BytesIO(raw)).read_message()


# ══ run_stdio 行为 ═════════════════════════════════════════════

def test_malformed_json_line_yields_parse_error_and_server_survives():
    """一条坏 JSON 只回一条 parse error，后续消息照常处理。"""
    good = encode_line({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    out = io.BytesIO()
    code = run_stdio(stdin=io.BytesIO(b"{not json\n" + good), stdout=out,
                     server=McpServer())
    assert code == 0
    lines = decode_lines(out.getvalue())
    assert lines[0]["error"]["code"] == -32700
    assert lines[0]["id"] is None
    assert any(x.get("id") == 3 and "result" in x for x in lines), "坏帧后应继续服务"


def test_response_uses_same_framing_as_request():
    """Content-Length 进来 -> Content-Length 回去；行进来 -> 行回去。"""
    payload = encode_message({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    out = io.BytesIO()
    run_stdio(stdin=io.BytesIO(payload), stdout=out, server=McpServer())
    assert out.getvalue().startswith(b"Content-Length:")

    out2 = io.BytesIO()
    run_stdio(stdin=io.BytesIO(encode_line({"jsonrpc": "2.0", "id": 1,
                                            "method": "ping"})),
              stdout=out2, server=McpServer())
    assert out2.getvalue().startswith(b"{")
    assert decode_lines(out2.getvalue())[0]["id"] == 1


def test_eof_exits_cleanly():
    out = io.BytesIO()
    assert run_stdio(stdin=io.BytesIO(b""), stdout=out, server=McpServer()) == 0
    assert out.getvalue() == b""


def test_exit_notification_stops_loop():
    data = encode_line({"jsonrpc": "2.0", "method": "exit"})
    out = io.BytesIO()
    assert run_stdio(stdin=io.BytesIO(data), stdout=out, server=McpServer()) == 0


# ══ 集成 ═══════════════════════════════════════════════════════

def test_stdio_roundtrip_through_real_subprocess():
    """只此一条起真进程：initialize -> tools/list -> exit，证明 stdio 接线是通的。"""
    payload = (
        encode_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"}})
        + encode_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                          "params": {}})
        + encode_line({"jsonrpc": "2.0", "method": "exit"})
    )
    proc = subprocess.run(
        [sys.executable, "-m", "backend.necessity.mcp.transport"],
        input=payload, capture_output=True, timeout=120, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    reader = FrameReader(io.BytesIO(proc.stdout))
    init = reader.read_message()
    assert init["id"] == 1
    assert init["result"]["serverInfo"]["name"] == "necessity"
    assert init["result"]["capabilities"]["tools"]["listChanged"] is False

    listed = reader.read_message()
    assert listed["id"] == 2
    assert {t["name"] for t in listed["result"]["tools"]} == EXPECTED_TOOLS


def test_reduce_minimize_full_report_on_real_repo(tmp_path):
    """§5.5 报告 + 沙箱隔离：单 hunk 改坏测试，用户工作区必须没被污染。"""
    repo = make_tiny_git_repo(tmp_path)
    diff = ("diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n"
            "@@ -1,1 +1,1 @@\n-VALUE = 1\n+VALUE = 999\n")
    s = McpServer()
    r = call(s, "reduce_minimize",
             {"repo": str(repo), "diff": diff, "targets": ["tests/"]})
    assert r["result"]["isError"] is False, content_text(r)
    structured = r["result"]["structuredContent"]
    # 必要方向：删掉它测试仍通过（基线本来就 pass）-> 全冗余
    assert structured["metrics"]["converged"] is True
    assert structured["metrics"]["redundancy_ratio"] == 1.0
    assert structured["original_diff"]["hunks"] == 1

    from backend.necessity.reduce import user_tree_is_clean
    clean, detail = user_tree_is_clean(str(repo))
    assert clean, detail


def test_context_lookup_returns_symbol_index_when_indexed(tmp_path):
    """已建索引 -> 返回 L1 符号索引，且 content_hash 与 core 同源。"""
    from backend.necessity.index.store import Store
    from backend.necessity.index.symbols import file_content_hash

    ws = tmp_path / "ws"
    ws.mkdir()
    content = "def alpha():\n    return 1\n"
    (ws / "m.py").write_text(content, encoding="utf-8")
    db = str(tmp_path / "idx.db")
    store = Store(db)
    ch = store.put_file_content(str(ws), "m.py", content)
    assert ch == file_content_hash(content)
    store.upsert_symbols(str(ws), "m.py", [
        {"qualified_name": "alpha", "name": "alpha", "kind": "function",
         "start_line": 0, "end_line": 1}], content_hash=ch)
    store.close()

    r = call(McpServer(), "context_lookup",
             {"workspace": str(ws), "path": "m.py", "db": db, "session_id": "s"})
    assert r["result"]["isError"] is False, content_text(r)
    structured = r["result"]["structuredContent"]
    assert not structured.get("degraded"), "已建索引不应降级"
    assert [x["qualified_name"] for x in structured["symbols"]] == ["alpha"]
    assert "alpha:1" in structured["body"]
    assert structured["content_hash"] == ch
    assert "原生" in structured["caveat"], "必须诚实标注 Context 的能力边界"


def test_context_lookup_degrades_when_not_indexed(tmp_path):
    """未建索引 -> degraded=True，不假装有内容。"""
    r = call(McpServer(), "context_lookup",
             {"workspace": str(tmp_path), "path": "nope.py",
              "db": str(tmp_path / "none.db")})
    assert r["result"]["isError"] is False
    assert r["result"]["structuredContent"]["degraded"] is True
    assert "原生" in r["result"]["structuredContent"]["caveat"]
