import pytest
import sys, asyncio, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from tools.base import ToolRegistry, ToolSpec, safe_resolve_path, truncate_output


class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        spec = ToolSpec(name="test_tool", description="A test", parameters={"type": "object"})
        reg.register(spec, lambda args, ws: f"result: {args}")
        tools = reg.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "test_tool"

    def test_list_tools_openai(self):
        reg = ToolRegistry()
        reg.register(
            ToolSpec(name="t1", description="d1", parameters={"type": "object"}),
            lambda args, ws: "ok"
        )
        tools = reg.list_tools_openai()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "t1"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_execute(self):
        reg = ToolRegistry()
        async def echo_handler(args, ws):
            return f"echo: {args.get('msg', '')}"
        reg.register(
            ToolSpec(name="echo", description="echo", parameters={"type": "object"}),
            echo_handler
        )
        result = await reg.execute("echo", {"msg": "hello"}, ".")
        assert result.success == True
        assert "echo: hello" in result.output

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = await reg.execute("nonexistent", {}, ".")
        assert result.success == False
        assert "Unknown" in result.error

    @pytest.mark.asyncio
    async def test_execute_error(self):
        reg = ToolRegistry()
        async def failer(args, ws):
            raise Exception("bang")
        reg.register(
            ToolSpec(name="failer", description="fails", parameters={"type": "object"}),
            failer
        )
        result = await reg.execute("failer", {}, ".")
        assert result.success == False

    def test_stats(self):
        reg = ToolRegistry()
        stats = reg.stats()
        assert "registered_tools" in stats
        assert "total_calls" in stats


class TestSafePath:
    def test_safe_path_in_workspace(self, tmp_path):
        ws = str(tmp_path)
        result = safe_resolve_path("foo.py", ws)
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_safe_path_traversal_blocked(self, tmp_path):
        ws = str(tmp_path)
        with pytest.raises(PermissionError):
            safe_resolve_path("../../../etc/passwd", ws)


class TestTruncate:
    def test_short_output(self):
        assert truncate_output("hello") == "hello"

    def test_long_output(self):
        long = "x" * 20000
        result = truncate_output(long, 1000)
        # truncate_output may exceed max_chars slightly due to truncation message
        assert len(result) <= 1050
        assert "truncated" in result