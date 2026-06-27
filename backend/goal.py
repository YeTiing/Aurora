import uuid
import time
import json
import os
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Literal

logger = logging.getLogger("aurora.goal")

GOALS_FILE = Path(os.environ.get("AURORA_HOME", ".aurora")) / "goals.json"

@dataclass
class Goal:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    objective: str = ""
    token_budget: Optional[int] = None
    tokens_used: int = 0
    turns_used: int = 0
    status: Literal["pending", "running", "complete", "blocked"] = "pending"
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "token_budget": self.token_budget,
            "tokens_used": self.tokens_used,
            "turns_used": self.turns_used,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        return cls(
            id=d.get("id", ""),
            objective=d.get("objective", ""),
            token_budget=d.get("token_budget"),
            tokens_used=d.get("tokens_used", 0),
            turns_used=d.get("turns_used", 0),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", time.time()),
            completed_at=d.get("completed_at"),
        )


class GoalManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._goals: list[Goal] = []
        self._active: Optional[Goal] = None
        self._load()

    def _persist_path(self) -> Path:
        return GOALS_FILE

    def _save(self) -> None:
        try:
            goals_file = self._persist_path()
            goals_file.parent.mkdir(parents=True, exist_ok=True)
            data = [g.to_dict() for g in self._goals]
            goals_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.debug("goal persist failed", exc_info=True)

    def _load(self) -> None:
        try:
            goals_file = self._persist_path()
            if not goals_file.exists():
                return
            data = json.loads(goals_file.read_text(encoding="utf-8"))
            self._goals = [Goal.from_dict(d) for d in data]
            # Restore active goal (last running one)
            for g in self._goals:
                if g.status == "running":
                    self._active = g
        except Exception:
            logger.debug("goal load failed", exc_info=True)

    def create_goal(self, objective: str, token_budget: Optional[int] = None) -> Goal:
        with self._lock:
            goal = Goal(objective=objective, token_budget=token_budget, status="running")
            self._goals.append(goal)
            self._active = goal
            self._save()
            return goal

    def update_goal(self, goal_id: str, status: Literal["complete", "blocked"]) -> Optional[Goal]:
        with self._lock:
            for g in self._goals:
                if g.id == goal_id:
                    g.status = status
                    g.completed_at = time.time()
                    if self._active and self._active.id == goal_id:
                        self._active = None
                    self._save()
                    return g
            return None

    def get_active_goal(self) -> Optional[Goal]:
        return self._active

    def track_tokens(self, n: int) -> bool:
        if not self._active:
            return True
        self._active.tokens_used += n
        if self._active.token_budget is not None and self._active.tokens_used > self._active.token_budget:
            return False
        return True

    def track_turn(self) -> None:
        with self._lock:
            if self._active:
                self._active.turns_used += 1

    def is_budget_exhausted(self) -> bool:
        if not self._active or self._active.token_budget is None:
            return False
        return self._active.tokens_used > self._active.token_budget

    def stats(self) -> dict:
        active = None
        if self._active:
            active = self._active.to_dict()
        return {
            "active_goal": active,
            "total_goals": len(self._goals),
            "goals": [g.to_dict() for g in self._goals[-20:]],
        }


goal_manager = GoalManager()
