# Tool Call Streaming JSON Aggregator
# Replicates Codex activeToolBlocks pattern for incremental JSON accumulation
from __future__ import annotations
import json, time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ActiveToolBlock:
    block_id: str
    tool_name: str = ""
    fragments: list[str] = field(default_factory=list)
    started_at: float = 0.0
    call_id: str = ""

    @property
    def raw_json(self) -> str:
        return "".join(self.fragments)

    @property
    def is_empty(self) -> bool:
        return len(self.fragments) == 0

    def try_parse(self) -> dict | None:
        raw = self.raw_json.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return None
        except json.JSONDecodeError:
            return None


@dataclass
class FinalizedToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


class ToolCallAggregator:
    def __init__(self):
        self.active_blocks: dict[str, ActiveToolBlock] = {}
        self._completed_calls: list[FinalizedToolCall] = []
        self._last_activity: float = time.time()

    def start_tool_block(self, block_id: str, tool_name: str = "", call_id: str = "") -> ActiveToolBlock:
        if block_id in self.active_blocks:
            return self.active_blocks[block_id]
        block = ActiveToolBlock(
            block_id=block_id,
            tool_name=tool_name,
            call_id=call_id or block_id,
            started_at=time.time(),
        )
        self.active_blocks[block_id] = block
        self._last_activity = time.time()
        return block

    def append_json_fragment(self, block_id: str, partial_json: str) -> bool:
        if block_id not in self.active_blocks:
            block = ActiveToolBlock(block_id=block_id, started_at=time.time())
            self.active_blocks[block_id] = block
        self.active_blocks[block_id].fragments.append(partial_json)
        self._last_activity = time.time()
        return True

    def finalize_tool_block(self, block_id: str) -> FinalizedToolCall | None:
        block = self.active_blocks.pop(block_id, None)
        if block is None:
            return None
        parsed = block.try_parse()
        if parsed is None:
            return None
        if "function" in parsed:
            func = parsed["function"]
            name = func.get("name", block.tool_name)
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw_args": args}
        else:
            name = parsed.pop("name", block.tool_name)
            args = parsed.pop("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw_args": args}
            if not args:
                args = parsed
        call = FinalizedToolCall(id=block.call_id, name=name, arguments=args)
        self._completed_calls.append(call)
        self._last_activity = time.time()
        return call

    def cancel_tool_block(self, block_id: str) -> bool:
        block = self.active_blocks.pop(block_id, None)
        return block is not None

    def reset(self) -> None:
        self.active_blocks.clear()
        self._completed_calls.clear()
        self._last_activity = time.time()

    @property
    def block_count(self) -> int:
        return len(self.active_blocks)

    @property
    def completed_count(self) -> int:
        return len(self._completed_calls)

    def get_snapshot(self) -> dict:
        blocks = {}
        for bid, block in self.active_blocks.items():
            blocks[bid] = {
                "tool_name": block.tool_name,
                "fragment_count": len(block.fragments),
                "raw_preview": block.raw_json[:200],
                "age_seconds": round(time.time() - block.started_at, 2),
                "call_id": block.call_id,
            }
        completed = [{"id": c.id, "name": c.name, "args_preview": str(c.arguments)[:200]} for c in self._completed_calls[-10:]]
        return {
            "active_block_count": len(blocks),
            "total_completed": len(self._completed_calls),
            "active_blocks": blocks,
            "recent_completed": completed,
        }

    def process_delta_event(self, event: dict) -> str | None:
        etype = event.get("type", "")
        call_id = event.get("call_id", event.get("id", ""))
        if "delta" in etype or "arguments" in etype:
            delta = event.get("delta", "")
            if delta:
                if call_id not in self.active_blocks:
                    self.start_tool_block(call_id, tool_name=event.get("name", ""), call_id=call_id)
                self.append_json_fragment(call_id, delta)
            if "done" in etype or "complete" in etype:
                if call_id in self.active_blocks:
                    self.active_blocks[call_id].tool_name = event.get("name", "")
            return None
        if "done" in etype or "complete" in etype:
            if call_id in self.active_blocks:
                name = event.get("name", self.active_blocks[call_id].tool_name)
                self.active_blocks[call_id].tool_name = name
                self.finalize_tool_block(call_id)
                return call_id
        return None


tool_call_aggregator = ToolCallAggregator()