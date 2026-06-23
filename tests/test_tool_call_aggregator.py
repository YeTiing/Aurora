"""Tests for Tool Call Streaming JSON Aggregator"""
import pytest
import json


class TestActiveToolBlock:
    def test_raw_json(self):
        from backend.agent.tool_call_aggregator import ActiveToolBlock
        block = ActiveToolBlock(block_id="call_1", tool_name="read_file")
        block.fragments.append('{"path": "/etc')
        block.fragments.append('/hosts"}')
        assert block.raw_json == '{"path": "/etc/hosts"}'

    def test_is_empty(self):
        from backend.agent.tool_call_aggregator import ActiveToolBlock
        block = ActiveToolBlock(block_id="x")
        assert block.is_empty is True
        block.fragments.append("x")
        assert block.is_empty is False

    def test_try_parse_valid(self):
        from backend.agent.tool_call_aggregator import ActiveToolBlock
        block = ActiveToolBlock(block_id="c1")
        block.fragments.append('{"name": "shell_command", "arguments": {"command": "ls"}}')
        parsed = block.try_parse()
        assert parsed is not None
        assert parsed["name"] == "shell_command"
        assert parsed["arguments"]["command"] == "ls"

    def test_try_parse_incomplete(self):
        from backend.agent.tool_call_aggregator import ActiveToolBlock
        block = ActiveToolBlock(block_id="c1")
        block.fragments.append('{"name": "shell_command", "arg')
        assert block.try_parse() is None

    def test_try_parse_empty(self):
        from backend.agent.tool_call_aggregator import ActiveToolBlock
        block = ActiveToolBlock(block_id="c1")
        assert block.try_parse() is None


class TestToolCallAggregator:
    def test_start_tool_block(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        block = agg.start_tool_block("call_1", "read_file")
        assert "call_1" in agg.active_blocks
        assert block.tool_name == "read_file"

    def test_start_duplicate_block(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        b1 = agg.start_tool_block("call_1", "a")
        b2 = agg.start_tool_block("call_1", "b")
        assert b1 is b2
        assert b1.tool_name == "a"

    def test_append_json_fragment(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.append_json_fragment("call_1", '{"name": "t1"')
        agg.append_json_fragment("call_1", ', "args": {"x": 1}}')
        assert agg.active_blocks["call_1"].raw_json == '{"name": "t1", "args": {"x": 1}}'

    def test_append_auto_creates_block(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.append_json_fragment("new_call", '{"name": "auto"}')
        assert "new_call" in agg.active_blocks
        assert agg.block_count == 1

    def test_finalize_success(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.append_json_fragment("c1", '{"name": "shell_command", "arguments": {"command": "echo hi"}}')
        result = agg.finalize_tool_block("c1")
        assert result is not None
        assert result.name == "shell_command"
        assert result.arguments["command"] == "echo hi"
        assert agg.block_count == 0
        assert agg.completed_count == 1

    def test_finalize_function_format(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.append_json_fragment("c1", '{"function": {"name": "do_thing", "arguments": "{\\"key\\": \\"val\\"}"}}')
        result = agg.finalize_tool_block("c1")
        assert result.name == "do_thing"
        assert result.arguments["key"] == "val"

    def test_finalize_unknown_block(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        assert agg.finalize_tool_block("nonexistent") is None

    def test_finalize_incomplete_json(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.append_json_fragment("c1", '{"name": "broken", "ar')
        result = agg.finalize_tool_block("c1")
        assert result is None
        assert agg.block_count == 0

    def test_cancel_tool_block(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.start_tool_block("c1", "test")
        assert agg.cancel_tool_block("c1") is True
        assert agg.cancel_tool_block("c1") is False

    def test_reset(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.append_json_fragment("c1", '{"name": "t1", "arguments": {}}')
        agg.finalize_tool_block("c1")
        agg.start_tool_block("c2", "t2")
        agg.reset()
        assert agg.block_count == 0
        assert agg.completed_count == 0

    def test_get_snapshot(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.start_tool_block("b1", "tool_a")
        agg.append_json_fragment("b1", '{"x":')
        snapshot = agg.get_snapshot()
        assert snapshot["active_block_count"] == 1
        assert "b1" in snapshot["active_blocks"]
        assert snapshot["active_blocks"]["b1"]["tool_name"] == "tool_a"

    def test_process_delta_event(self):
        from backend.agent.tool_call_aggregator import ToolCallAggregator
        agg = ToolCallAggregator()
        agg.process_delta_event({"type": "response.function_call_arguments.delta", "call_id": "abc", "delta": '{"name": "hello"'})
        agg.process_delta_event({"type": "response.function_call_arguments.delta", "call_id": "abc", "delta": ', "arguments": {"x":1}}'})
        assert agg.active_blocks["abc"].raw_json == '{"name": "hello", "arguments": {"x":1}}'


class TestFinalizedToolCall:
    def test_defaults(self):
        from backend.agent.tool_call_aggregator import FinalizedToolCall
        ftc = FinalizedToolCall(id="abc", name="test")
        assert ftc.id == "abc"
        assert ftc.name == "test"
        assert ftc.arguments == {}