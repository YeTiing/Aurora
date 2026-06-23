# Agent记忆系统 — 三层架构：工作记忆 + 情景记忆 + 语义记忆
"""
▒▒▒ Aurora Memory Architecture ▒▒▒

Working Memory  (session)  → 当前任务上下文、最近交互、活跃计划
Episodic Memory (短期持久)   → 过往任务会话记录、成功/失败经验
Semantic Memory (长期持久)   → 用户偏好、项目惯例、习得模式

检索策略：先查Working→再查Episodic→最后Semantic，结果合并到上下文
"""

from __future__ import annotations
import json, time, hashlib, threading
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════

class MemoryType(Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryImportance(Enum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class MemoryEntry:
    """通用记忆条目"""
    id: str
    type: MemoryType
    content: str
    summary: str = ""
    importance: MemoryImportance = MemoryImportance.MEDIUM
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0

    def touch(self):
        self.last_accessed = time.time()
        self.access_count += 1

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type.value,
            "content": self.content, "summary": self.summary,
            "importance": self.importance.value, "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MemoryEntry:
        return cls(
            id=d["id"],
            type=MemoryType(d.get("type", "semantic")),
            content=d.get("content", ""),
            summary=d.get("summary", ""),
            importance=MemoryImportance(d.get("importance", 1)),
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", time.time()),
            last_accessed=d.get("last_accessed", time.time()),
            access_count=d.get("access_count", 0),
        )


# ═══════════════════════════════════════════════════════════════
# Working Memory — 会话级短期记忆
# ═══════════════════════════════════════════════════════════════

@dataclass
class WorkingContext:
    """当前会话的工作上下文"""
    task_description: str = ""
    active_goal: str = ""
    recent_files: list[str] = field(default_factory=list)     # 最近操作的文件
    recent_errors: list[str] = field(default_factory=list)    # 最近遇到的错误
    key_insights: list[str] = field(default_factory=list)     # 关键发现
    user_preferences: dict = field(default_factory=dict)      # 会话中明确的偏好
    tool_stats: dict = field(default_factory=dict)            # 各工具使用次数
    decision_log: list[str] = field(default_factory=list)     # 关键决策记录

    def add_file(self, path: str):
        if path not in self.recent_files:
            self.recent_files.append(path)
            self.recent_files = self.recent_files[-20:]  # 只保留最近20个

    def add_error(self, error: str):
        self.recent_errors.append(error)
        self.recent_errors = self.recent_errors[-10:]

    def add_insight(self, insight: str):
        if insight not in self.key_insights:
            self.key_insights.append(insight)
            self.key_insights = self.key_insights[-10:]

    def record_decision(self, decision: str):
        self.decision_log.append(f"[{time.strftime('%H:%M:%S')}] {decision}")
        self.decision_log = self.decision_log[-20:]

    def record_tool_use(self, tool_name: str):
        self.tool_stats[tool_name] = self.tool_stats.get(tool_name, 0) + 1

    def to_context_string(self) -> str:
        """将工作记忆转成可注入LLM上下文的字符串"""
        parts = []
        if self.task_description:
            parts.append(f"**Current Task**: {self.task_description}")
        if self.active_goal:
            parts.append(f"**Active Goal**: {self.active_goal}")
        if self.key_insights:
            parts.append("**Key Insights**:\n" + "\n".join(f"- {i}" for i in self.key_insights[-5:]))
        if self.recent_errors:
            parts.append("**Recent Errors**:\n" + "\n".join(f"- {e[:150]}" for e in self.recent_errors[-3:]))
        if self.recent_files:
            parts.append("**Recent Files**: " + ", ".join(f"`{f}`" for f in self.recent_files[-10:]))
        if self.decision_log:
            parts.append("**Key Decisions**:\n" + "\n".join(f"- {d}" for d in self.decision_log[-5:]))
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "task_description": self.task_description,
            "active_goal": self.active_goal,
            "recent_files": self.recent_files,
            "recent_errors": self.recent_errors,
            "key_insights": self.key_insights,
            "user_preferences": self.user_preferences,
            "tool_stats": self.tool_stats,
            "decision_log": self.decision_log,
        }

    def clear(self):
        self.recent_files.clear()
        self.recent_errors.clear()
        self.key_insights.clear()
        self.decision_log.clear()
        self.tool_stats.clear()


# ═══════════════════════════════════════════════════════════════
# Episodic Memory — 过往任务存储
# ═══════════════════════════════════════════════════════════════

@dataclass
class Episode:
    """一次完整的任务执行记录"""
    episode_id: str
    session_id: str
    task: str
    plan: list[dict] = field(default_factory=list)
    outcome: str = ""           # success / partial / failure
    summary: str = ""
    key_learnings: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    llm_calls: int = 0
    tokens_used: int = 0
    duration_sec: float = 0.0
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id, "session_id": self.session_id,
            "task": self.task, "plan": self.plan, "outcome": self.outcome,
            "summary": self.summary, "key_learnings": self.key_learnings,
            "files_changed": self.files_changed, "tools_used": self.tools_used,
            "llm_calls": self.llm_calls, "tokens_used": self.tokens_used,
            "duration_sec": self.duration_sec, "errors": self.errors,
            "created_at": self.created_at, "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Episode:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class EpisodicMemory:
    """情景记忆 — 存储过往任务经验，按需检索"""

    def __init__(self, db_path: str = "", max_episodes: int = 500):
        self._episodes: dict[str, Episode] = {}
        self._max = max_episodes
        self._lock = threading.Lock()
        self.db_path = db_path

    @property
    def count(self) -> int:
        return len(self._episodes)

    def record(self, episode: Episode):
        with self._lock:
            self._episodes[episode.episode_id] = episode
            if len(self._episodes) > self._max:
                oldest = min(self._episodes.values(), key=lambda e: e.created_at)
                del self._episodes[oldest.episode_id]

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        outcome: str = "",
        limit: int = 10,
    ) -> list[Episode]:
        """按关键词/标签/结果搜索历史任务"""
        q_lower = query.lower()
        results = []
        for ep in self._episodes.values():
            score = 0
            if q_lower:
                if q_lower in ep.task.lower():
                    score += 10
                if q_lower in ep.summary.lower():
                    score += 5
                for l in ep.key_learnings:
                    if q_lower in l.lower():
                        score += 3
            if tags:
                if all(t in ep.tags for t in tags):
                    score += 5
            if outcome and ep.outcome == outcome:
                score += 2
            if score > 0:
                results.append((score, ep))

        results.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in results[:limit]]

    def similar_tasks(self, task: str, limit: int = 5) -> list[Episode]:
        """查找与给定任务相似的历史记录"""
        task_words = set(task.lower().split())
        scored = []
        for ep in self._episodes.values():
            ep_words = set(ep.task.lower().split())
            overlap = len(task_words & ep_words)
            if overlap > 0:
                scored.append((overlap, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:limit]]

    def get_learnings(self, limit: int = 20) -> list[str]:
        """搜集所有成功任务的 lessons learned"""
        learnings = []
        for ep in self._episodes.values():
            if ep.outcome == "success":
                learnings.extend(ep.key_learnings)
        return learnings[:limit]

    def to_dict(self) -> dict:
        return {eid: ep.to_dict() for eid, ep in self._episodes.items()}

    def load_from_dict(self, data: dict):
        self._episodes = {k: Episode.from_dict(v) for k, v in data.items()}


# ═══════════════════════════════════════════════════════════════
# Semantic Memory — 长期知识积累
# ═══════════════════════════════════════════════════════════════

@dataclass
class SemanticFact:
    """一条语义记忆 — 习得的知识"""
    fact_id: str
    category: str               # convention / preference / pattern / rule / bug_fix / tip
    content: str
    importance: MemoryImportance = MemoryImportance.MEDIUM
    confidence: float = 1.0     # 置信度，多次验证会提高
    source_episode: str = ""    # 来源情景
    reinforcement: int = 1      # 强化次数
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def reinforce(self):
        self.reinforcement += 1
        self.confidence = min(1.0, self.confidence + 0.1)

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "category": self.category,
            "content": self.content, "importance": self.importance.value,
            "confidence": self.confidence, "source_episode": self.source_episode,
            "reinforcement": self.reinforcement, "created_at": self.created_at,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SemanticFact:
        return cls(
            fact_id=d["fact_id"], category=d.get("category", "convention"),
            content=d.get("content", ""),
            importance=MemoryImportance(d.get("importance", 1)),
            confidence=d.get("confidence", 1.0),
            source_episode=d.get("source_episode", ""),
            reinforcement=d.get("reinforcement", 1),
            created_at=d.get("created_at", time.time()),
            tags=d.get("tags", []),
        )


class SemanticMemory:
    """语义记忆 — 积累项目特定知识"""

    CATEGORIES = [
        "convention",    # 项目代码惯例
        "preference",    # 用户偏好
        "pattern",       # 常见模式/解决方案
        "rule",          # 必须遵守的规则
        "bug_fix",       # 修复经验
        "tip",           # 优化技巧
        "architecture",  # 架构知识
    ]

    def __init__(self, db_path: str = ""):
        self._facts: dict[str, SemanticFact] = {}
        self._lock = threading.Lock()
        self.db_path = db_path

    @property
    def count(self) -> int:
        return len(self._facts)

    def add_fact(
        self,
        content: str,
        category: str = "convention",
        importance: MemoryImportance = MemoryImportance.MEDIUM,
        tags: list[str] | None = None,
        source_episode: str = "",
    ) -> str:
        fact_id = hashlib.md5(f"{category}:{content}".encode()).hexdigest()[:12]
        with self._lock:
            if fact_id in self._facts:
                self._facts[fact_id].reinforce()
                return fact_id
            self._facts[fact_id] = SemanticFact(
                fact_id=fact_id, category=category, content=content,
                importance=importance, source_episode=source_episode,
                tags=tags or [],
            )
        return fact_id

    def search(
        self,
        query: str = "",
        category: str = "",
        tags: list[str] | None = None,
        min_confidence: float = 0.5,
        limit: int = 10,
    ) -> list[SemanticFact]:
        q_lower = query.lower()
        results = []
        for f in self._facts.values():
            if f.confidence < min_confidence:
                continue
            if category and f.category != category:
                continue
            score = 0
            if q_lower:
                if q_lower in f.content.lower():
                    score += 10
                    # Boost by importance
                    score += f.importance.value * 2
                    # Boost by reinforcement
                    score += min(f.reinforcement, 5)
            if tags:
                if all(t in f.tags for t in tags):
                    score += 5
            if score > 0:
                results.append((score, f))

        results.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in results[:limit]]

    def get_conventions(self) -> list[str]:
        """获取所有代码惯例"""
        return [f.content for f in self._facts.values() if f.category == "convention"]

    def get_preferences(self) -> list[str]:
        """获取所有用户偏好"""
        return [f.content for f in self._facts.values() if f.category == "preference"]

    def get_by_category(self, category: str) -> list[SemanticFact]:
        return [f for f in self._facts.values() if f.category == category]

    def to_context_string(self) -> str:
        """生成可注入LLM上下文的格式"""
        parts = []
        conventions = self.get_conventions()
        prefs = self.get_preferences()
        if conventions:
            parts.append("**Project Conventions**:\n" + "\n".join(f"- {c}" for c in conventions[:10]))
        if prefs:
            parts.append("**User Preferences**:\n" + "\n".join(f"- {p}" for p in prefs[:10]))
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {fid: f.to_dict() for fid, f in self._facts.items()}

    def load_from_dict(self, data: dict):
        self._facts = {k: SemanticFact.from_dict(v) for k, v in data.items()}


# ═══════════════════════════════════════════════════════════════
# Memory Hub — 统一入口
# ═══════════════════════════════════════════════════════════════

class MemoryHub:
    """三层记忆系统的统一协调器"""

    def __init__(self, db_dir: str = ".aurora_memory", use_db: bool = True):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.use_db = use_db

        self.working = WorkingContext()
        self.episodic = EpisodicMemory(str(self.db_dir / "episodes.json") if use_db else "")
        self.semantic = SemanticMemory(str(self.db_dir / "semantic.json") if use_db else "")

        self._lock = threading.Lock()
        self._stats = {"searches": 0, "records": 0, "learnings": 0}

    # ── 记录 ──

    def record_episode(self, episode: Episode):
        with self._lock:
            self.episodic.record(episode)
            self._stats["records"] += 1

            # 自动从成功任务中提取语义知识
            if episode.outcome == "success":
                for learning in episode.key_learnings:
                    self.semantic.add_fact(
                        content=learning,
                        category=self._classify_learning(learning),
                        importance=MemoryImportance.MEDIUM,
                        source_episode=episode.episode_id,
                    )
                    self._stats["learnings"] += 1

    @staticmethod
    def _classify_learning(learning: str) -> str:
        lower = learning.lower()
        if any(w in lower for w in ("should", "always", "never", "must", "rule")):
            return "rule"
        if any(w in lower for w in ("pattern", "template", "boilerplate")):
            return "pattern"
        if any(w in lower for w in ("convention", "style", "naming", "format")):
            return "convention"
        if any(w in lower for w in ("fix", "bug", "issue", "error")):
            return "bug_fix"
        if any(w in lower for w in ("prefer", "like", "use", "用", "喜欢")):
            return "preference"
        return "tip"

    def record_working_insight(self, insight: str):
        self.working.add_insight(insight)

    def record_working_decision(self, decision: str):
        self.working.record_decision(decision)

    def record_working_file(self, filepath: str):
        self.working.add_file(filepath)

    def record_working_error(self, error: str):
        self.working.add_error(error)

    def record_working_tool_use(self, tool: str):
        self.working.record_tool_use(tool)

    def add_semantic_fact(self, content: str, category: str = "convention", **kwargs):
        return self.semantic.add_fact(content, category=category, **kwargs)

    # ── 检索 ──

    def search(self, query: str, limit: int = 10) -> dict:
        """全局搜索 — 跨三层记忆"""
        self._stats["searches"] += 1
        episodes = self.episodic.search(query=query, limit=min(limit, 5))
        facts = self.semantic.search(query=query, limit=min(limit, 5))

        return {
            "episodes": [e.to_dict() for e in episodes],
            "facts": [f.to_dict() for f in facts],
            "similar_tasks": [e.to_dict() for e in self.episodic.similar_tasks(query)],
        }

    def get_context_for_llm(self, task: str = "") -> str:
        """生成完整的LLM上下文注入"""
        parts = []

        # 1. Working memory
        wm = self.working.to_context_string()
        if wm:
            parts.append(f"## Working Memory\n{wm}")

        # 2. Semantic memory
        sm = self.semantic.to_context_string()
        if sm:
            parts.append(f"## Semantic Knowledge\n{sm}")

        # 3. Relevant episodes
        if task:
            similar = self.episodic.similar_tasks(task, limit=3)
            if similar:
                ep_lines = ["## Similar Past Tasks"]
                for ep in similar:
                    ep_lines.append(f"- [{ep.outcome}] {ep.task[:100]}")
                    if ep.key_learnings:
                        ep_lines.append(f"  Learnings: {'; '.join(ep.key_learnings[:3])}")
                parts.append("\n".join(ep_lines))

        return "\n\n".join(parts)

    # ── 持久化 ──

    def save(self):
        """保存到磁盘"""
        if not self.use_db:
            return
        with self._lock:
            ep_path = self.db_dir / "episodes.json"
            sm_path = self.db_dir / "semantic.json"
            wm_path = self.db_dir / "working.json"

            ep_path.write_text(json.dumps(self.episodic.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            sm_path.write_text(json.dumps(self.semantic.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            wm_path.write_text(json.dumps(self.working.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self):
        """从磁盘加载"""
        if not self.use_db:
            return

        ep_path = self.db_dir / "episodes.json"
        sm_path = self.db_dir / "semantic.json"

        if ep_path.exists():
            data = json.loads(ep_path.read_text(encoding="utf-8"))
            self.episodic.load_from_dict(data)

        if sm_path.exists():
            data = json.loads(sm_path.read_text(encoding="utf-8"))
            self.semantic.load_from_dict(data)

    def clear_working(self):
        self.working.clear()

    @property
    def stats(self) -> dict:
        return {
            **self._stats,
            "episodes": self.episodic.count,
            "facts": self.semantic.count,
            "working_files": len(self.working.recent_files),
        }


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

_memory_hub: MemoryHub | None = None


def get_memory() -> MemoryHub:
    global _memory_hub
    if _memory_hub is None:
        _memory_hub = MemoryHub()
    return _memory_hub


def init_memory(db_dir: str = ".aurora_memory") -> MemoryHub:
    global _memory_hub
    _memory_hub = MemoryHub(db_dir=db_dir)
    _memory_hub.load()
    return _memory_hub