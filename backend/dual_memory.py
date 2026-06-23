
"""Dual-File Memory System — Hermes-style MEMORY.md + USER.md.

Persistent, agent-managed memory with:
- AGENT_MEMORY.md: facts, conventions, environment (2,200 char limit)
- USER_PROFILE.md: user preferences, style, habits (1,375 char limit)  
- Curator: periodic auto-review, deduplication, compression
- Honcho-like: user trait extraction from conversation
- Session recall: cross-session search via ChromaDB
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ── Constants ──

MEMORY_DIR = Path(os.environ.get("AURORA_HOME", ".aurora")) / "memories"
AGENT_MEMORY_FILE = "AGENT_MEMORY.md"
USER_PROFILE_FILE = "USER_PROFILE.md"

MAX_AGENT_MEMORY_CHARS = 2200  # ~800 tokens
MAX_USER_PROFILE_CHARS = 1375  # ~500 tokens
ENTRY_DELIMITER = "§"

# ── Data Classes ──

@dataclass
class MemoryEntry:
    """A single memory entry."""
    text: str
    index: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    source: str = "agent"  # agent | curator | honcho | user

    def to_line(self) -> str:
        return f"{ENTRY_DELIMITER} {self.text}"

    @staticmethod
    def from_line(line: str, index: int) -> "MemoryEntry":
        text = line.strip()
        if text.startswith(ENTRY_DELIMITER):
            text = text[1:].strip()
        return MemoryEntry(text=text, index=index, created_at=time.time())


@dataclass
class MemoryStore:
    """A named memory store (AGENT_MEMORY or USER_PROFILE)."""
    name: str
    file_path: Path
    max_chars: int
    entries: list[MemoryEntry] = field(default_factory=list)
    _dirty: bool = False

    def load(self) -> "MemoryStore":
        """Load entries from disk."""
        self.entries.clear()
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            # Create default
            if "agent" in self.name.lower():
                default = "This is a new workspace. Aurora will learn about your projects and preferences."
            else:
                default = "New user. Aurora is learning about you."
            self.file_path.write_text(
                f"# {self.name}\n# One entry per line starting with §\n\n{ENTRY_DELIMITER} {default}\n",
                encoding="utf-8"
            )

        content = self.file_path.read_text(encoding="utf-8")
        for i, line in enumerate(content.split('\n')):
            stripped = line.strip()
            if stripped and (stripped.startswith(ENTRY_DELIMITER) or not stripped.startswith('#')):
                self.entries.append(MemoryEntry.from_line(stripped, i))
        return self

    def save(self) -> None:
        """Persist entries to disk."""
        header = f"# {self.name}\n# One entry per line starting with §\n# Max {self.max_chars} chars\n\n"
        body = '\n'.join(e.to_line() for e in self.entries)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(header + body + '\n', encoding="utf-8")
        self._dirty = False

    @property
    def char_count(self) -> int:
        return sum(len(e.text) for e in self.entries)

    @property
    def usage_pct(self) -> int:
        if self.max_chars == 0:
            return 0
        return min(100, int(self.char_count / self.max_chars * 100))

    def add(self, text: str, source: str = "agent") -> tuple[bool, str]:
        """Add an entry. Returns (success, message)."""
        new_chars = self.char_count + len(text)
        if new_chars > self.max_chars:
            return False, (
                f"Cannot add: would use {new_chars}/{self.max_chars} chars "
                f"({self.usage_pct}% → overflow). Remove or shorten entries first."
            )
        entry = MemoryEntry(text=text, index=len(self.entries),
                            created_at=time.time(), source=source)
        self.entries.append(entry)
        self._dirty = True
        return True, f"Added. Now at {self.char_count}/{self.max_chars} chars ({self.usage_pct}%)."

    def replace(self, index: int, new_text: str, source: str = "agent") -> tuple[bool, str]:
        """Replace entry at index. Returns (success, message)."""
        if index < 0 or index >= len(self.entries):
            return False, f"Invalid index: {index} (have {len(self.entries)} entries)"
        old_len = len(self.entries[index].text)
        new_total = self.char_count - old_len + len(new_text)
        if new_total > self.max_chars:
            return False, (
                f"Cannot replace: would use {new_total}/{self.max_chars} chars. "
                f"Shorten new text by {new_total - self.max_chars} chars."
            )
        self.entries[index].text = new_text
        self.entries[index].updated_at = time.time()
        self.entries[index].source = source
        self._dirty = True
        return True, f"Replaced entry {index}. Now at {self.char_count}/{self.max_chars} chars ({self.usage_pct}%)."

    def remove(self, index: int) -> tuple[bool, str]:
        """Remove entry at index. Returns (success, message)."""
        if index < 0 or index >= len(self.entries):
            return False, f"Invalid index: {index}"
        removed = self.entries.pop(index).text[:40]
        # Re-index
        for i, e in enumerate(self.entries):
            e.index = i
        self._dirty = True
        return True, f"Removed entry {index}: '{removed}...'. Now at {self.char_count}/{self.max_chars} chars ({self.usage_pct}%)."

    def list_entries(self) -> list[dict]:
        """List all entries with metadata."""
        return [
            {
                "index": i,
                "text": e.text,
                "source": e.source,
                "length": len(e.text),
            }
            for i, e in enumerate(self.entries)
        ]

    def to_system_prompt(self) -> str:
        """Format as system prompt injection block."""
        label = "MEMORY (your personal notes)" if "agent" in self.name.lower() else "USER PROFILE"
        header = (
            f"══════════════════════════════════════════════\n"
            f"{label}\n"
            f"[{self.char_count}/{self.max_chars} chars — {self.usage_pct}%]\n"
            f"══════════════════════════════════════════════"
        )
        body = '\n'.join(e.to_line() for e in self.entries)
        return f"{header}\n{body}"


# ── Curator ──

@dataclass
class CuratorState:
    """State tracking for the memory curator."""
    last_run_at: float = 0.0
    run_count: int = 0
    last_summary: str = ""
    paused: bool = False

    STATE_FILE = "curator_state.json"

    @classmethod
    def load(cls) -> "CuratorState":
        path = MEMORY_DIR / cls.STATE_FILE
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls(**data)
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (MEMORY_DIR / self.STATE_FILE).write_text(
            json.dumps(self.__dict__, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )


class MemoryCurator:
    """Background curator: dedupe, compress, extract insights."""

    def __init__(self, agent_store: MemoryStore, user_store: MemoryStore):
        self.agent_store = agent_store
        self.user_store = user_store
        self.state = CuratorState.load()

    def should_run(self, interval_hours: int = 168, min_idle_hours: int = 2) -> bool:
        """Check if enough time has passed since last run."""
        if self.state.paused:
            return False
        elapsed = time.time() - self.state.last_run_at
        return elapsed > interval_hours * 3600

    def run_lightweight(self) -> dict:
        """Lightweight curation without LLM — dedupe by similarity."""
        results = {"agent": self._deduplicate(self.agent_store),
                   "user": self._deduplicate(self.user_store)}

        self.state.last_run_at = time.time()
        self.state.run_count += 1
        self.state.last_summary = json.dumps(results, ensure_ascii=False)
        self.state.save()

        if self.agent_store._dirty:
            self.agent_store.save()
        if self.user_store._dirty:
            self.user_store.save()
        return results

    def _deduplicate(self, store: MemoryStore) -> dict:
        """Remove near-duplicate entries using simple overlap ratio."""
        removed = []
        i = 0
        while i < len(store.entries):
            j = i + 1
            while j < len(store.entries):
                # Simple Jaccard-like overlap
                words_i = set(store.entries[i].text.lower().split())
                words_j = set(store.entries[j].text.lower().split())
                if words_i and words_j:
                    overlap = len(words_i & words_j) / min(len(words_i), len(words_j))
                    if overlap > 0.8:  # 80%+ word overlap
                        removed.append({
                            "kept": store.entries[i].text[:60],
                            "removed": store.entries[j].text[:60],
                            "overlap": round(overlap, 2),
                        })
                        store.entries.pop(j)
                        store._dirty = True
                        continue
                j += 1
            i += 1
        return {"removed_count": len(removed), "removed": removed}


# ── Honcho-like User Trait Extractor ──

class UserTraitExtractor:
    """Extract user traits/preferences from conversation and update USER_PROFILE."""

    TRAIT_PATTERNS = {
        "language": [
            (r"用中文|说中文|Chinese|中文", "Prefers Chinese"),
            (r"用英文|English|英文", "Prefers English"),
        ],
        "style": [
            (r"简洁|简短|少说|别啰嗦|straightforward|concise", "Prefers concise answers"),
            (r"详细|多说|解释|elaborate|explain more", "Prefers detailed explanations"),
        ],
        "tech_stack": [
            (r"Python|python3?|\.py", "Uses Python"),
            (r"TypeScript|React|Next\.js|Node\.js", "Uses TypeScript/React ecosystem"),
            (r"Java|Spring|SpringBoot", "Uses Java/Spring Boot"),
            (r"Rust|cargo|\.rs", "Uses Rust"),
            (r"Go|golang", "Uses Go"),
        ],
        "work_style": [
            (r"不要问|别确认|直接做|not ask|just do", "Wants direct action, minimal confirmation"),
            (r"先问我|确认|ask first|confirm", "Prefers confirmation before actions"),
        ],
    }

    def extract(self, conversation_text: str, existing_profile: MemoryStore) -> list[str]:
        """Extract new traits from conversation and add to profile."""
        found = []
        for category, patterns in self.TRAIT_PATTERNS.items():
            for pattern, trait in patterns:
                if re.search(pattern, conversation_text, re.IGNORECASE):
                    # Check if already in profile
                    already = any(trait.lower() in e.text.lower()
                                  for e in existing_profile.entries)
                    if not already:
                        found.append(trait)

        # Deduplicate and add unique traits
        unique = list(dict.fromkeys(found))
        added = []
        for trait in unique[:3]:  # Max 3 new traits per extraction
            ok, msg = existing_profile.add(trait, source="honcho")
            if ok:
                added.append(trait)

        if added:
            existing_profile.save()
        return added


# ── Session Recall ──

class SessionRecall:
    """Search past sessions for relevant context using ChromaDB."""

    def __init__(self):
        self._db = None

    def _ensure_db(self):
        if self._db is None:
            try:
                import chromadb
                self._db = chromadb.PersistentClient(
                    path=str(MEMORY_DIR.parent / "semantic_memory.db")
                )
            except Exception:
                self._db = False
        return self._db

    def index_session(self, session_id: str, summary: str, metadata: dict = None) -> bool:
        """Index a session summary for later recall."""
        db = self._ensure_db()
        if not db:
            return False
        try:
            collection = db.get_or_create_collection("session_recall")
            collection.add(
                documents=[summary],
                metadatas=[metadata or {}],
                ids=[session_id],
            )
            return True
        except Exception:
            return False

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """Search past sessions relevant to current query."""
        db = self._ensure_db()
        if not db:
            return []
        try:
            collection = db.get_or_create_collection("session_recall")
            results = collection.query(query_texts=[query], n_results=top_k)
            if results and results.get("documents"):
                return [
                    {
                        "session_id": results["ids"][0][i],
                        "content": results["documents"][0][i][:300],
                        "metadata": results.get("metadatas", [[{}]])[0][i],
                    }
                    for i in range(len(results["ids"][0]))
                ]
        except Exception:
            pass
        return []


# ── Global Manager ──

class DualMemoryManager:
    """Central manager for dual-file memory, curator, and user modeling."""

    def __init__(self, memory_dir: str | Path = None):
        self.memory_dir = Path(memory_dir) if memory_dir else MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self.agent_memory = MemoryStore(
            name="AGENT_MEMORY",
            file_path=self.memory_dir / AGENT_MEMORY_FILE,
            max_chars=MAX_AGENT_MEMORY_CHARS,
        ).load()

        self.user_profile = MemoryStore(
            name="USER_PROFILE",
            file_path=self.memory_dir / USER_PROFILE_FILE,
            max_chars=MAX_USER_PROFILE_CHARS,
        ).load()

        self.curator = MemoryCurator(self.agent_memory, self.user_profile)
        self.trait_extractor = UserTraitExtractor()
        self.session_recall = SessionRecall()

    def get_system_prompt_injection(self) -> str:
        """Get combined memory + profile for system prompt."""
        parts = []
        if self.agent_memory.entries:
            parts.append(self.agent_memory.to_system_prompt())
        if self.user_profile.entries:
            parts.append(self.user_profile.to_system_prompt())
        return '\n\n'.join(parts)

    def process_conversation_turn(self, user_message: str, assistant_response: str) -> dict:
        """Process a conversation turn: extract traits, save if dirty."""
        result = {"traits_extracted": []}

        # Extract user traits from this turn
        combined = user_message + " " + (assistant_response or "")[:200]
        if len(combined) > 50:
            traits = self.trait_extractor.extract(combined, self.user_profile)
            result["traits_extracted"] = traits

        # Auto-save if dirty
        if self.agent_memory._dirty:
            self.agent_memory.save()
        if self.user_profile._dirty:
            self.user_profile.save()

        return result

    def end_session(self, session_id: str, summary: str) -> dict:
        """Session cleanup: curator run, index summary."""
        result = {}

        # Run lightweight curator
        if self.curator.should_run():
            curation = self.curator.run_lightweight()
            result["curation"] = curation

        # Index session for recall
        if summary:
            recall_ok = self.session_recall.index_session(session_id, summary,
                                                          {"timestamp": time.time()})
            result["indexed"] = recall_ok

        # Auto-save
        if self.agent_memory._dirty:
            self.agent_memory.save()
        if self.user_profile._dirty:
            self.user_profile.save()

        return result

    def search_past_sessions(self, query: str) -> list[dict]:
        """Recall relevant past sessions."""
        return self.session_recall.search(query)

    def stats(self) -> dict:
        return {
            "agent_memory": {
                "entries": len(self.agent_memory.entries),
                "chars": self.agent_memory.char_count,
                "max": self.agent_memory.max_chars,
                "usage_pct": self.agent_memory.usage_pct,
            },
            "user_profile": {
                "entries": len(self.user_profile.entries),
                "chars": self.user_profile.char_count,
                "max": self.user_profile.max_chars,
                "usage_pct": self.user_profile.usage_pct,
            },
            "curator": {
                "runs": self.curator.state.run_count,
                "last_run": self.curator.state.last_run_at,
                "paused": self.curator.state.paused,
            },
        }


# ── Singleton ──

_dual_memory: DualMemoryManager | None = None


def get_dual_memory() -> DualMemoryManager:
    global _dual_memory
    if _dual_memory is None:
        _dual_memory = DualMemoryManager()
    return _dual_memory
