
"""Dual-File Memory System — Hermes-style MEMORY.md + USER.md.

Persistent, agent-managed memory with:
- AGENT_MEMORY.md: facts, conventions, environment (2,200 char limit)
- USER_PROFILE.md: user preferences, style, habits (1,375 char limit)  
- Curator: periodic auto-review, deduplication, compression
- Honcho-like: user trait extraction from conversation
- Session recall: cross-session search via ChromaDB
"""

from __future__ import annotations

import asyncio
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

    def stats(self) -> dict:
        """Return statistics about this memory store."""
        return {
            "name": self.name,
            "entries": len(self.entries),
            "char_count": self.char_count,
            "max_chars": self.max_chars,
            "usage_pct": self.usage_pct,
            "dirty": self._dirty,
        }

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



# ═════════════════════════════════════════════════════════════
# LAYER 2-7: Full Closed Loop Memory
# ═════════════════════════════════════════════════════════════

import sqlite3, textwrap, re as _regex
from datetime import datetime, timezone

SKILL_DIR = Path(os.environ.get("AURORA_HOME", ".aurora")) / "skills"
SKILL_ARCHIVE = SKILL_DIR / ".archive"
AURORA_DIR = Path(os.environ.get("AURORA_HOME", ".aurora"))

# ── Skill Meta ──

@dataclass
class SkillMeta:
    name: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    views: int = 0
    uses: int = 0
    patches: int = 0
    last_used_at: float = 0.0
    state: str = "active"
    source: str = "agent"

    def to_header(self) -> str:
        return f"""<!-- skill-meta
name: {self.name}
created: {self.created_at}
updated: {self.updated_at}
views: {self.views}
uses: {self.uses}
patches: {self.patches}
last_used: {self.last_used_at}
state: {self.state}
source: {self.source}
-->"""

    @staticmethod
    def from_header(text: str) -> "SkillMeta":
        m = SkillMeta(name="unknown")
        for line in text.split("\n"):
            line = line.strip()
            for fld in ["name","created","updated","views","uses","patches","last_used","state","source"]:
                if line.startswith(fld + ":"):
                    v = line.split(":",1)[1].strip()
                    if fld in ("views","uses","patches"): setattr(m, fld, int(v or 0))
                    elif fld in ("created","updated","last_used"): setattr(m, fld+"_at", float(v or 0))
                    else: setattr(m, fld, v)
        return m

    def touch(self): self.uses += 1; self.last_used_at = time.time()
    def viewed(self): self.views += 1
    def patched(self): self.patches += 1; self.updated_at = time.time()


# ── Skill Manager ──

class SkillManager:
    def __init__(self, d: Path = None):
        self.dir = d or SKILL_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        SKILL_ARCHIVE.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[dict]:
        r = []
        for f in self.dir.glob("*.md"):
            m = self._meta(f)
            if m: r.append({"name":m.name,"state":m.state,"uses":m.uses,"views":m.views,"patches":m.patches,"source":m.source,"last_used":m.last_used_at})
        for f in SKILL_ARCHIVE.glob("*.md"):
            m = self._meta(f)
            if m: r.append({"name":m.name,"state":"archived","uses":m.uses})
        return r

    def get(self, name: str):
        f = self.dir / f"{name}.md"
        if not f.exists(): f = SKILL_ARCHIVE / f"{name}.md"
        if not f.exists(): return None, None
        c = f.read_text("utf-8")
        m = self._meta(f)
        if m: m.viewed(); self._write(f, c, m)
        return c, m

    def create(self, name: str, desc: str, body: str, src: str = "agent") -> Path:
        name = _regex.sub(r"[^a-z0-9_-]", "-", name.lower())[:64]
        m = SkillMeta(name=name, source=src, created_at=time.time())
        c = f"{m.to_header()}\n\n# {name}\n\n{desc}\n\n## Instructions\n\n{body}\n"
        p = self.dir / f"{name}.md"
        p.write_text(c, "utf-8")
        return p

    def patch(self, name: str, body: str) -> bool:
        f = self.dir / f"{name}.md"
        if not f.exists(): return False
        c = f.read_text("utf-8"); m = self._meta(f)
        if not m: return False
        if "## Instructions" in c:
            c = c.split("## Instructions")[0] + "## Instructions\n\n" + body + "\n"
        m.patched(); self._write(f, c, m)
        return True

    def use(self, name: str):
        f = self.dir / f"{name}.md"
        if f.exists():
            c = f.read_text("utf-8"); m = self._meta(f)
            if m: m.touch(); self._write(f, c, m)

    def archive(self, name: str) -> bool:
        f = self.dir / f"{name}.md"
        if not f.exists(): return False
        c = f.read_text("utf-8"); m = self._meta(f)
        if m: m.state = "archived"; self._write(f, c, m)
        import shutil; shutil.move(str(f), str(SKILL_ARCHIVE / f.name))
        return True

    def _meta(self, p: Path):
        try:
            c = p.read_text("utf-8")
            if "<!-- skill-meta" in c:
                s = c.index("<!-- skill-meta"); e = c.index("-->", s) + 3
                return SkillMeta.from_header(c[s:e])
        except: pass
        return None

    def _write(self, p: Path, c: str, m: SkillMeta):
        h = m.to_header()
        c = _regex.sub(r"<!-- skill-meta.*?-->", h, c, flags=_regex.DOTALL) if "<!-- skill-meta" in c else h + "\n\n" + c
        p.write_text(c, "utf-8")


# ── Honcho Dialectic ──

@dataclass
class PeerCard:
    traits: list = field(default_factory=list)
    preferences: list = field(default_factory=list)
    knowledge_levels: dict = field(default_factory=dict)
    contradictions: list = field(default_factory=list)
    last_updated: float = 0.0

    def context(self) -> str:
        p = []
        if self.traits: p.append("Traits: " + ", ".join(self.traits))
        if self.preferences: p.append("Preferences: " + "; ".join(self.preferences))
        return "\n".join(p)


class HonchoDialectic:
    def __init__(self, ctx_cadence=5, dial_cadence=10, dial_depth=2):
        self.ctx_cadence = ctx_cadence
        self.dial_cadence = dial_cadence
        self.dial_depth = dial_depth
        self.peer = PeerCard()
        self._turns = 0
        self._facts = []
        self._summary = ""
        self._path = MEMORY_DIR / "peer_card.json"
        if self._path.exists():
            try: self.peer = PeerCard(**json.loads(self._path.read_text("utf-8")))
            except: pass

    def record(self, user_msg: str, _resp: str):
        self._turns += 1
        if len(user_msg) > 20: self._facts.append(user_msg[:200])

    def should_base(self) -> bool: return self._turns > 0 and self._turns % self.ctx_cadence == 0
    def should_dialectic(self) -> bool: return self._turns > 0 and self._turns % self.dial_cadence == 0

    def depth_for(self, qlen: int) -> int:
        if qlen > 500: return min(3, self.dial_depth + 1)
        if qlen > 200: return self.dial_depth
        return max(1, self.dial_depth - 1)

    def cold_prompt(self) -> str:
        f = "\n".join(f"  - {x}" for x in self._facts[-10:])
        return f"Build user model from conversation:\n{f}\n\nReturn JSON: {{traits:[],preferences:[],knowledge_levels:{{}},contradictions:[]}}. Pure JSON, no markdown."

    def warm_prompt(self) -> str:
        f = "\n".join(f"  - {x}" for x in self._facts[-5:])
        return f"Update user model. Existing: {self.peer.context()}\nNew: {f}\n\nReturn JSON: {{traits:[],preferences:[],knowledge_levels:{{}},contradictions:[],removed_traits:[]}}. Pure JSON."

    def apply(self, d: dict):
        for t in d.get("removed_traits", []):
            if t in self.peer.traits: self.peer.traits.remove(t)
        for t in d.get("traits", []):
            if t not in self.peer.traits: self.peer.traits.append(t)
        for p in d.get("preferences", []):
            if p not in self.peer.preferences: self.peer.preferences.append(p)
        for k, v in d.get("knowledge_levels", {}).items():
            self.peer.knowledge_levels[k] = v
        for c in d.get("contradictions", []):
            if c not in self.peer.contradictions: self.peer.contradictions.append(c)
        self.peer.last_updated = time.time()
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self.peer.__dict__, indent=2, ensure_ascii=False), "utf-8")

    def set_summary(self, s: str): self._summary = s

    def prompt_injection(self) -> str:
        p = []
        c = self.peer.context()
        if c: p.append(f"USER MODEL:\n{c}")
        if self._summary: p.append(f"SESSION CONTEXT:\n{self._summary}")
        return "\n\n".join(p)


# ── Full Curator with LLM ──

@dataclass  
class CuratorCfg:
    last_run_at: float = 0.0
    run_count: int = 0
    last_summary: str = ""
    paused: bool = False
    archive_days: int = 30
    stale_days: int = 14
    interval_hrs: int = 168
    min_idle_hrs: int = 2
    prune_builtins: bool = True

    @classmethod
    def load(cls):
        p = MEMORY_DIR / "curator.json"
        if p.exists():
            try:
                d = json.loads(p.read_text("utf-8"))
                return cls(**{k:v for k,v in d.items() if k in cls.__dataclass_fields__})
            except: pass
        return cls()

    def save(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (MEMORY_DIR/"curator.json").write_text(json.dumps(self.__dict__, indent=2, ensure_ascii=False), "utf-8")


class Curator:
    def __init__(self, agent: MemoryStore, user: MemoryStore, skills: SkillManager):
        self.agent = agent; self.user = user; self.skills = skills
        self.cfg = CuratorCfg.load()
        self._lock = asyncio.Lock()

    def should(self) -> bool:
        if self.cfg.paused: return False
        return time.time() - self.cfg.last_run_at > self.cfg.interval_hrs * 3600

    def light(self) -> dict:
        r = {"agent": self._dedup(self.agent), "user": self._dedup(self.user), "skills": self._stale()}
        self._done(r); return r

    async def full(self, llm) -> dict:
        async with self._lock:
            r = {"agent": self._dedup(self.agent), "user": self._dedup(self.user)}
            try:
                r["memory"] = await self._llm_mem(llm)
                r["skills"] = await self._llm_skills(llm)
            except Exception as e:
                r["error"] = str(e)
            r["staled"] = self._stale()
            self._done(r); return r

    def _done(self, r):
        self.cfg.last_run_at = time.time(); self.cfg.run_count += 1
        self.cfg.last_summary = json.dumps(r, ensure_ascii=False)
        self.cfg.save()
        if self.agent._dirty: self.agent.save()
        if self.user._dirty: self.user.save()

    def _dedup(self, s: MemoryStore) -> dict:
        rem = []; i = 0
        while i < len(s.entries):
            j = i + 1
            while j < len(s.entries):
                wa = set(s.entries[i].text.lower().split()); wb = set(s.entries[j].text.lower().split())
                if wa and wb and len(wa & wb) / min(len(wa), len(wb)) > 0.8:
                    rem.append({"kept":s.entries[i].text[:60],"removed":s.entries[j].text[:60]})
                    s.entries.pop(j); s._dirty = True; continue
                j += 1
            i += 1
        return {"removed": len(rem)}

    def _stale(self) -> dict:
        now = time.time(); sa, aa = [], []
        for sk in self.skills.all():
            if sk.get("state") == "archived": continue
            if sk.get("source") == "hub": continue
            if not self.cfg.prune_builtins and sk.get("source") == "bundled": continue
            lu = sk.get("last_used", 0)
            if lu and now - lu > self.cfg.archive_days * 86400:
                self.skills.archive(sk["name"]); aa.append(sk["name"])
            elif lu and now - lu > self.cfg.stale_days * 86400:
                sa.append(sk["name"])
        return {"staled": sa, "archived": aa}

    async def _llm_mem(self, llm) -> dict:
        me = "\n".join(f"  [{i}] {e.text}" for i, e in enumerate(self.agent.entries))
        ue = "\n".join(f"  [{i}] {e.text}" for i, e in enumerate(self.user.entries))
        p = f"Review memory:\nAGENT:\n{me}\nUSER:\n{ue}\n\nSuggest merges, rewrites, removals. Return JSON: {{agent_merges:[[i,j]],agent_rewrites:{{i:text}},agent_removes:[i]}}. Pure JSON."
        try:
            r = await llm.chat_simple(user_message=p, system_prompt="You are a memory curator. Return only valid JSON.", max_tokens=800)
            t = r.content if hasattr(r,"content") else str(r)
            m = _regex.search(r"\{.*\}", t, _regex.DOTALL)
            if m:
                plan = json.loads(m.group())
                for merge in plan.get("agent_merges",[]):
                    if len(merge)==2 and 0<=merge[0]<merge[1]<len(self.agent.entries):
                        self.agent.replace(merge[0], self.agent.entries[merge[0]].text+"; "+self.agent.entries[merge[1]].text, "curator")
                        self.agent.remove(merge[1])
                return {"applied": True}
        except: pass
        return {"result": "no changes"}

    async def _llm_skills(self, llm) -> dict:
        active = [s for s in self.skills.all() if s.get("state") != "archived"]
        if len(active) < 2: return {"result": "too few skills"}
        sl = "\n".join(f"  - {s['name']} (uses={s.get('uses',0)}, source={s.get('source','agent')})" for s in active)
        p = f"Audit skills:\n{sl}\n\nSuggest: merge similar, archive low-usage, rename badly named. Return JSON: {{merges:[[a,b,new_name]],archive:[name],rename:{{old:new}}}}. Pure JSON."
        try:
            r = await llm.chat_simple(user_message=p, system_prompt="You are a skill curator. Return only valid JSON.", max_tokens=800)
            t = r.content if hasattr(r,"content") else str(r)
            m = _regex.search(r"\{.*\}", t, _regex.DOTALL)
            if m:
                plan = json.loads(m.group())
                for name in plan.get("archive",[])[:5]: self.skills.archive(name)
                return {"applied": True}
        except: pass
        return {"result": "no changes"}


# ── Memory Nudge ──

class MemoryNudge:
    def __init__(self, every=10):
        self.every = every; self._turns = 0; self._learn = []; self._last = 0.0

    def learn(self, ev: str):
        self._learn.append(ev)
        if len(self._learn) > 20: self._learn = self._learn[-15:]

    def should(self) -> bool:
        self._turns += 1
        return self._turns % self.every == 0 and self._learn and time.time() - self._last > 300

    def prompt(self) -> str:
        ls = "\n".join(f"  · {l}" for l in self._learn[-5:])
        self._last = time.time(); self._learn.clear()
        return f"[Memory Nudge] Recent learnings:\n{ls}\n\nConsider saving important knowledge with `memory add` or `skill_create`."

    def end_prompt(self, mem_count: int) -> str:
        self._learn.clear()
        return f"[Session Ending] You have {mem_count} memory entries. Review — anything worth saving permanently? Use `memory add`."


# ── FTS5 Session Store ──

class FTSSessions:
    def __init__(self, db: Path = None):
        self.db = db or (AURORA_DIR / "fts5.db")
        self._c = None

    @property
    def c(self):
        if self._c is None:
            self._c = sqlite3.connect(str(self.db))
            self._c.execute("PRAGMA journal_mode=WAL")
            self._c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(sid,summary,body,created_at,tokenize='porter unicode61')")
            self._c.execute("CREATE TABLE IF NOT EXISTS meta(sid TEXT PRIMARY KEY,summary TEXT,turns INT,created REAL,ended REAL)")
            self._c.commit()
        return self._c

    def add(self, sid: str, summary: str, body: str = "", turns: int = 0):
        try:
            now = time.time()
            self.c.execute("INSERT OR REPLACE INTO meta VALUES(?,?,?,?,?)", (sid, summary[:1000], turns, now, now))
            self.c.execute("INSERT INTO fts VALUES(?,?,?,?)", (sid, summary[:2000], (body or summary)[:5000], now))
            self.c.commit()
        except: pass

    def search(self, q: str, n: int = 5) -> list[dict]:
        try:
            rows = self.c.execute("SELECT sid,summary,body,rank FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?", (q, n)).fetchall()
            return [{"sid":r[0],"summary":r[1][:300],"body":(r[2] or r[1])[:500]} for r in rows]
        except Exception:
            rows = self.c.execute("SELECT sid,summary FROM meta WHERE summary LIKE ? ORDER BY ended DESC LIMIT ?", (f"%{q}%", n)).fetchall()
            return [{"sid":r[0],"summary":r[1][:300]} for r in rows]

    def recent(self, n: int = 10) -> list[dict]:
        try:
            rows = self.c.execute("SELECT sid,summary,turns,ended FROM meta ORDER BY ended DESC LIMIT ?", (n,)).fetchall()
            return [{"sid":r[0],"summary":r[1][:200],"turns":r[2],"ended":r[3]} for r in rows]
        except: return []


# ── Closed-Loop Master Manager ──

class ClosedLoopMemory:
    """All 7 layers in one manager."""

    def __init__(self, d: Path = None):
        d = d or MEMORY_DIR; d.mkdir(parents=True, exist_ok=True)
        self.agent_memory = MemoryStore("AGENT_MEMORY", d / AGENT_MEMORY_FILE, MAX_AGENT_MEMORY_CHARS).load()
        self.user_profile = MemoryStore("USER_PROFILE", d / USER_PROFILE_FILE, MAX_USER_PROFILE_CHARS).load()
        self.skills = SkillManager()
        self.curator = Curator(self.agent_memory, self.user_profile, self.skills)
        self.honcho = HonchoDialectic()
        self.nudge = MemoryNudge()
        self.fts5 = FTSSessions()
        self.provider = BuiltinMemoryProvider(self.agent_memory, self.user_profile)
        self._turns_since_last_skill_check = 0

    def system_prompt(self, query: str = "") -> str:
        parts = []
        mem = self.provider.context()
        if mem: parts.append(mem)
        h = self.honcho.prompt_injection()
        if h: parts.append(h)
        if query:
            past = self.fts5.search(query, 2)
            if past:
                parts.append("PAST SESSIONS:\n" + "\n".join(f"  [{r['sid'][:8]}] {r['summary'][:200]}" for r in past))
        return "\n\n".join(parts)

    def process_turn(self, user: str, resp: str) -> dict:
        r = {}
        self.honcho.record(user, resp)
        if len(resp) > 100: self.nudge.learn(f"User: {user[:100]}")

        # ── Auto-record triggers ──

        # 1. User explicitly states a preference → auto-save to USER_PROFILE
        pref_triggers = [
            (r"(以后|从现在[起开]|记住|下次|always|forever|从现在开始).{0,20}(用|说|写|做|喜欢|需要|想要|prefer)", "stated"),
            (r"(我是|我用|我做|我写).{0,30}(的|开发|程序员|engineer|developer|backend|frontend)", "identity"),
            (r"(不要|别|停止|stop|don.t|never).{0,20}(问我|确认|啰嗦|verbose|长篇|废话)", "style"),
        ]
        for pattern, ptype in pref_triggers:
            if re.search(pattern, user, re.IGNORECASE):
                already = any(pattern in e.text.lower() for e in self.user_profile.entries
                             for pattern in [user.lower()[:40]])
                if not already:
                    self.user_profile.add(user[:200], source="auto")
                    r.setdefault("auto_recorded", []).append({"store": "user", "type": ptype, "text": user[:200]})
                break

        # 2. Agent produced a significant response (>500 chars) in a non-trivial context
        #    → extract key facts for AGENT_MEMORY
        if len(resp) > 200 and len(user) > 15:
            # Check if this looks like a coding task result
            task_markers = [
                r"(已|已经|完成|done|finished|好了|created|built|fixed|implemented|deployed|configured)",
                r"(```|文件|file|patch|commit|deploy|install|build|test)",
            ]
            is_task = any(re.search(m, resp, re.IGNORECASE) for m in task_markers)
            if is_task:
                # Extract the first sentence as a summary
                # Extract meaningful summary (first 2 sentences or 200 chars)
                parts = resp.replace("。", ".").split(".")
                summary = ".".join(parts[:2]).strip()[:200]
                if not summary or len(summary) < 15:
                    summary = resp[:200]
                if len(summary) >= 15:
                    # Only save if not a near-duplicate
                    existing_texts = [e.text.lower()[:50] for e in self.agent_memory.entries]
                    if summary.lower()[:50] not in existing_texts:
                        self.agent_memory.add(f"Task completed: {summary}", source="auto")
                        r.setdefault("auto_recorded", []).append({"store": "agent", "type": "task", "text": summary})

        # 3. Complex multi-turn task → mark for potential skill creation
        if self._turns_since_last_skill_check is None:
            self._turns_since_last_skill_check = 0
        self._turns_since_last_skill_check += 1
        if self._turns_since_last_skill_check >= 6 and len(resp) > 400:
            r["suggest_skill"] = (
                f"[Auto] This was a complex task ({self._turns_since_last_skill_check} turns). "
                f"Consider creating a skill with `memory skill_create` if this pattern is reusable."
            )
            self._turns_since_last_skill_check = 0

        # 4. Honcho dialectic auto-apply (if dialectic_needed flag set externally and result provided)
        #    This is handled by the caller passing in dialectic_result

        # ── Nudge check ──
        if self.nudge.should(): r["nudge"] = self.nudge.prompt()

        if self.honcho.should_dialectic(): r["dialectic_needed"] = True

        if self.agent_memory._dirty: self.agent_memory.save()
        if self.user_profile._dirty: self.user_profile.save()
        return r

    async def end_session(self, sid: str, summary: str = "", body: str = "", turns: int = 0, llm=None) -> dict:
        r = {}
        if llm and body and not summary:
            try:
                resp = await llm.chat_simple(user_message=f"Summarize this session in 1-2 sentences (Chinese if appropriate): {body[:3000]}", max_tokens=200)
                summary = (resp.content if hasattr(resp,"content") else str(resp))[:500]
                self.honcho.set_summary(summary)
            except: pass
        if summary:
            self.fts5.add(sid, summary, body, turns)
            r["indexed"] = True
        if self.curator.should():
            r["curation"] = await self.curator.full(llm) if llm else self.curator.light()
        r["nudge"] = self.nudge.end_prompt(len(self.agent_memory.entries))
        self.agent_memory.save(); self.user_profile.save()
        return r

    def search(self, q: str, n: int = 5) -> list[dict]:
        return self.fts5.search(q, n)

    def stats(self) -> dict:
        s = self.skills.all()
        return {
            "agent_memory": {"entries": len(self.agent_memory.entries), "chars": self.agent_memory.char_count, "usage_pct": self.agent_memory.usage_pct},
            "user_profile": {"entries": len(self.user_profile.entries), "chars": self.user_profile.char_count, "usage_pct": self.user_profile.usage_pct},
            "curator": {"runs": self.curator.cfg.run_count, "paused": self.curator.cfg.paused},
            "skills": {"total": len(s), "active": sum(1 for x in s if x.get("state")!="archived"), "archived": sum(1 for x in s if x.get("state")=="archived")},
            "honcho": {"turns": self.honcho._turns, "traits": len(self.honcho.peer.traits)},
            "fts5": {"sessions": len(self.fts5.recent(100))},
        }


# ── Provider Interface ──

class MemoryProvider:
    name: str = "base"
    async def search(self, q: str, n: int = 5) -> list[dict]: raise NotImplementedError
    async def store(self, k: str, v: str, m: dict = None) -> bool: raise NotImplementedError
    def context(self) -> str: return ""


class BuiltinMemoryProvider(MemoryProvider):
    name = "builtin"
    def __init__(self, a: MemoryStore, u: MemoryStore): self.a = a; self.u = u
    async def search(self, q: str, n: int = 5) -> list[dict]:
        ql = q.lower(); r = []
        for e in self.a.entries + self.u.entries:
            if ql in e.text.lower(): r.append({"text":e.text})
        return r[:n]
    async def store(self, k: str, v: str, m: dict = None) -> bool:
        t = self.u if k.startswith("user.") else self.a
        return t.add(v, source="provider")[0]
    def context(self) -> str:
        p = []
        if self.a.entries: p.append(self.a.to_system_prompt())
        if self.u.entries: p.append(self.u.to_system_prompt())
        return "\n\n".join(p)


# ── Singleton ──

_closed_loop: ClosedLoopMemory | None = None

def get_closed_loop() -> ClosedLoopMemory:
    global _closed_loop
    if _closed_loop is None: _closed_loop = ClosedLoopMemory()
    return _closed_loop

def get_dual_memory(): return get_closed_loop()

# Alias old class names for backward compat
DualMemoryManager = ClosedLoopMemory
MemoryCurator = Curator
UserTraitExtractor = HonchoDialectic
SessionRecall = FTSSessions
