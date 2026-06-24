"""MagicDocs - Auto-maintaining documentation files.

When a file with '# MAGIC DOC: [title]' header is read, it registers for
periodic background updates using Aurora's forked subagent.

Each time the conversation produces tool calls, tracked magic docs
may be updated with new learnings from the conversation.
"""
from __future__ import annotations
import asyncio, re, time, threading, hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAGIC_DOC_PATTERN = re.compile(r"^#\s*MAGIC\s+DOC:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
ITALICS_PATTERN = re.compile(r"^[_*](.+?)[_*]\s*$")


@dataclass
class MagicDoc:
    path: str
    title: str = ""
    instructions: str = ""
    content_hash: str = ""
    last_updated: float = 0.0
    update_cooldown_sec: float = 60.0


class MagicDocsManager:
    def __init__(self, cooldown_seconds: float = 60.0):
        self._docs: dict[str, MagicDoc] = {}
        self._lock = threading.Lock()
        self._cooldown = cooldown_seconds
        self._update_lock = asyncio.Lock()

    @staticmethod
    def detect(content: str) -> dict | None:
        match = MAGIC_DOC_PATTERN.search(content)
        if not match or not match[1]:
            return None
        title = match[1].strip()
        instructions = ""
        after = content[match.end():]
        nlm = re.match(r"\s*\n(?:\s*\n)?(.+?)(?:\n|$)", after)
        if nlm and ITALICS_PATTERN.match(nlm[1]):
            instructions = ITALICS_PATTERN.match(nlm[1])[1].strip()
        return {"title": title, "instructions": instructions}

    def register(self, file_path: str, content: str):
        detected = self.detect(content)
        if not detected:
            return
        content_hash = hashlib.md5(content.encode()).hexdigest()
        with self._lock:
            if file_path in self._docs:
                self._docs[file_path].title = detected["title"]
                self._docs[file_path].instructions = detected.get("instructions", "")
                self._docs[file_path].content_hash = content_hash
            else:
                self._docs[file_path] = MagicDoc(
                    path=file_path, title=detected["title"],
                    instructions=detected.get("instructions", ""),
                    content_hash=content_hash,
                    update_cooldown_sec=self._cooldown,
                    last_updated=time.time(),
                )

    def should_update(self, file_path: str) -> bool:
        doc = self._docs.get(file_path)
        if not doc:
            return False
        return (time.time() - doc.last_updated) >= doc.update_cooldown_sec

    def mark_updated(self, file_path: str):
        doc = self._docs.get(file_path)
        if doc:
            doc.last_updated = time.time()

    def build_update_prompt(self, file_path: str, current_content: str, conversation: str) -> str:
        doc = self._docs.get(file_path)
        if not doc:
            return ""
        instr = f"\nAdditional instructions: {doc.instructions}" if doc.instructions else ""
        return f"""Maintain this Magic Doc: {doc.title}

Current contents of {file_path}:
<doc>
{current_content[:8000]}
</doc>{instr}

Conversation to learn from:
<conversation>
{conversation[:6000]}
</conversation>

Identify NEW learnings. Return ONLY updated Markdown content, preserving
the "# MAGIC DOC: {doc.title}" header exactly. Update in-place, remove outdated info.
If nothing substantial to add, return "NO_CHANGES"."""

    @property
    def tracked_count(self) -> int:
        return len(self._docs)

    def list_all(self) -> list[dict]:
        return [{
            "path": d.path, "title": d.title,
            "instructions": d.instructions,
            "last_updated": d.last_updated, "hash": d.content_hash,
        } for d in self._docs.values()]


magic_docs_manager = MagicDocsManager()
