"""MCP server 测试（一）：协议面 + 工具调用 + Guard 边界。

设计要点：**不 spawn 真 MCP client、不起子进程**（唯一例外是 stdio 集成
测试）。`McpServer.handle_message` 是纯函数式的请求处理器，直接调用即可
覆盖整个协议面 —— 这也是把它设计成可直接调用的原因。

重点验证两件容易做错的事：
  1. Guard 边界：`constraint_guard` 不在 tools/list，且调用它得到的是
     「MCP 做不到」的解释而不是普通「未知工具」。
  2. 工具抛异常必须变成 isError 结果，**服务端还活着**（宿主会话不能断）。

分帧 / stdio 在 test_mcp_framing.py。
"""
import pytest

from tests._necessity_mcp_helpers import EXPECTED_TOOLS, McpServer, call, content_text, req, rpc
from backend.necessity.mcp.base import Tool, obj_schema
from backend.necessity.mcp.registry import tool_schemas


# ══ initialize ══════════════════════════════════════════════════

def test_initialize_returns_required_fields():
    s = McpServer()
    r = rpc(req("initialize", {"protocolVersion": "2024-11-05",
                               "clientInfo": {"name": "t", "version": "1"}}, 1), s)
    res = r["result"]
    assert res["protocolVersion"] == "2024-11-05"
    assert res["serverInfo"]["name"] == "necessity"
    assert res["serverInfo"]["version"]
    assert res["capabilities"]["tools"] == {"listChanged": False}
    # 不声明未实现的 capability —— 声明了就是对宿主撒谎
    assert "resources" not in res["capabilities"]
    assert "prompts" not in res["capabilities"]
    assert s.initialized is True


def test_second_initialize_is_handled_sanely():
    """重复 initialize 不能断会话：按已完成握手应答，且信息可更新。"""
    s = McpServer()
    r1 = rpc(req("initialize", {"protocolVersion": "2024-11-05"}, 1), s)
    r2 = rpc(req("initialize", {"protocolVersion": "2024-11-05",
                                "clientInfo": {"name": "x"}}, 2), s)
    assert r1["result"]["protocolVersion"] == r2["result"]["protocolVersion"]
    assert r2.get("error") is None
    assert s.client_info == {"name": "x"}


def test_notifications_initialized_accepted_without_response():
    s = McpServer()
    assert s.handle_message({"jsonrpc": "2.0",
                             "method": "notifications/initialized"}) is None
    assert s.initialized is True


# ══ tools/list ══════════════════════════════════════════════════

def test_tools_list_has_exactly_four_tools():
    s = McpServer()
    tools = rpc(req("tools/list", {}, 1), s)["result"]["tools"]
    assert {t["name"] for t in tools} == EXPECTED_TOOLS
    assert len(tools) == 4


@pytest.mark.parametrize("name,required,types", [
    ("reduce_minimize", {"repo"}, {"repo": "string", "direction": "string",
                                   "max_test_runs": "integer", "targets": "array"}),
    ("reduce_redundancy", {"repo"}, {"repo": "string", "diff": "string"}),
    ("attribution_report", {"session_id"}, {"session_id": "string", "db": "string"}),
    ("context_lookup", {"workspace", "path"},
     {"workspace": "string", "path": "string", "mode": "string", "lines": "array"}),
])
def test_each_tool_schema_is_valid(name, required, types):
    entry = {t["name"]: t for t in tool_schemas()}[name]
    schema = entry["inputSchema"]
    assert schema["type"] == "object"
    assert required <= set(schema["required"])
    for field, typ in types.items():
        assert schema["properties"][field]["type"] == typ, f"{name}.{field}"
    assert entry["description"]


def test_guard_is_absent_from_tools_list():
    names = {t["name"] for t in tool_schemas()}
    assert "constraint_guard" not in names
    assert not any("guard" in n for n in names), "Guard 不得以任何别名出现在 MCP"


def test_calling_guard_explains_mcp_cannot_intercept():
    """调用 Guard 必须得到「MCP 拦截不了别的工具」这条理由，而非泛泛的未知工具。"""
    s = McpServer()
    r = call(s, "constraint_guard", {})
    assert "error" in r, "Guard 调用不应被当成合法工具执行"
    assert r["error"]["code"] == -32601
    msg = r["error"]["message"]
    assert "MCP" in msg and "拦截" in msg
    assert "宿主集成" in msg          # 指向正确出路，而不是让 Agent 干瞪眼
    assert rpc(req("tools/list", {}, 2), s)["result"]["tools"]


# ══ tools/call 正常路径 ════════════════════════════════════════

def test_reduce_redundancy_returns_content_shape(repo_root):
    s = McpServer()
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n-x\n+y\n")
    r = call(s, "reduce_redundancy", {"repo": repo_root, "diff": diff})
    assert r["result"]["isError"] is False
    assert r["result"]["structuredContent"]["confirmed"] is False
    assert "冗余率" in content_text(r)


def test_attribution_report_without_trace_returns_unknown_not_crash():
    """无 db -> 无轨迹 -> unknown（而不是猜一个类别），且证据不变式成立。"""
    s = McpServer()
    r = call(s, "attribution_report", {"session_id": "s-1"})
    assert r["result"]["isError"] is False
    att = r["result"]["structuredContent"]["attribution"]
    assert att["primary"] == "unknown"
    assert att["method"] == "unattributable"
    assert att["evidence"] == []
    assert "gate6" in r["result"]["structuredContent"]


@pytest.fixture(scope="session")
def repo_root():
    from tests._necessity_mcp_helpers import ROOT
    return str(ROOT)


# ══ 工具异常：isError 且服务端存活 ══════════════════════════════

def test_bad_args_return_iserror_and_server_stays_alive(repo_root):
    s = McpServer()
    r = call(s, "reduce_redundancy", {}, 1)                    # 缺必需参数
    assert r["result"]["isError"] is True
    assert "repo" in content_text(r)

    r2 = call(s, "reduce_minimize",
              {"repo": repo_root, "diff": "x", "max_test_runs": True}, 2)
    assert r2["result"]["isError"] is True
    assert "bool" in content_text(r2)                          # true 不当 1 收

    r3 = call(s, "reduce_redundancy", {"repo": repo_root, "diff": "x"}, 3)
    assert r3["result"]["isError"] is True                     # 无可解析 hunk

    # —— 服务端仍存活：再调一个工具必须成功 ——
    assert call(s, "attribution_report", {"session_id": "alive"}, 4)[
        "result"]["isError"] is False


def test_missing_path_file_raises_iserror_not_crash(repo_root):
    s = McpServer()
    missing = repo_root + "/definitely-missing.diff"
    r = call(s, "reduce_redundancy", {"repo": repo_root, "diff_path": missing}, 1)
    assert r["result"]["isError"] is True
    assert "definitely-missing.diff" in content_text(r)
    assert call(s, "attribution_report", {"session_id": "ok"}, 2)[
        "result"]["isError"] is False


def test_unexpected_tool_exception_becomes_iserror():
    """工具内部抛任意异常 -> isError（不是 JSON-RPC error、更不是进程退出）。"""
    def boom(_params):
        raise RuntimeError("模拟 core 内部崩溃")

    s = McpServer(tools={"boom": Tool(
        name="boom", description="test", input_schema=obj_schema({}, []), fn=boom)})
    r = call(s, "boom", {})
    assert r["result"]["isError"] is True
    assert "RuntimeError" in content_text(r)
    assert "模拟" in r["result"]["structuredContent"]["error"]
    assert r["result"]["structuredContent"]["tool"] == "boom"


def test_tool_error_carries_structured_detail():
    def bad(_params):
        from backend.necessity.mcp.base import ToolError
        raise ToolError("参数 'x' 类型错误")

    s = McpServer(tools={"bad": Tool(
        name="bad", description="test", input_schema=obj_schema({}, []), fn=bad)})
    r = call(s, "bad", {})
    assert r["result"]["isError"] is True
    assert r["result"]["structuredContent"]["code"] == -32602
    assert "仍在运行" in content_text(r), "错误里应告诉宿主服务端还活着"


def test_unknown_tool_name_is_method_not_found():
    s = McpServer()
    r = call(s, "no_such_tool", {})
    assert r["error"]["code"] == -32601
    assert "reduce_minimize" in r["error"]["message"], "应列出可用工具"


# ══ JSON-RPC 协议错误 ══════════════════════════════════════════

def test_unknown_method_returns_32601():
    s = McpServer()
    r = rpc(req("resources/list", {}, 7), s)
    assert r["id"] == 7
    assert r["error"]["code"] == -32601


def test_bad_params_returns_32602():
    s = McpServer()
    r = rpc({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
             "params": [1, 2]}, s)                     # params 不是对象
    assert r["error"]["code"] == -32602

    r2 = rpc(req("tools/call", {"arguments": {}}, 9), s)   # 缺 name
    assert r2["error"]["code"] == -32602
    assert "name" in r2["error"]["message"]

    r3 = rpc(req("tools/call", {"name": "reduce_minimize",
                                "arguments": "nope"}, 10), s)
    assert r3["error"]["code"] == -32602


def test_missing_id_is_treated_as_notification():
    """JSON-RPC 2.0：没有 id 就不能回应 —— 必须返回 None，不能瞎发响应。"""
    s = McpServer()
    assert s.handle_message({"jsonrpc": "2.0", "method": "tools/list"}) is None


def test_shutdown_and_exit():
    s = McpServer()
    assert rpc(req("shutdown", {}, 1), s).get("result") == {}
    assert s.should_exit is False, "shutdown 不应退出，exit 才退出"
    assert rpc(req("exit", {}, 2), s).get("result") == {}
    assert s.should_exit is True
