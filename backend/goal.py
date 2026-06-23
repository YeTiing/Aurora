import uuid
import time
from dataclasses import dataclass, field
from typing import Optional, Literal

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


class GoalManager:
    def __init__(self):
        self._goals: list[Goal] = []
        self._active: Optional[Goal] = None

    def create_goal(self, objective: str, token_budget: Optional[int] = None) -> Goal:
        goal = Goal(objective=objective, token_budget=token_budget, status="running")
        self._goals.append(goal)
        self._active = goal
        return goal

    def update_goal(self, goal_id: str, status: Literal["complete", "blocked"]) -> Optional[Goal]:
        for g in self._goals:
            if g.id == goal_id:
                g.status = status
                g.completed_at = time.time()
                if self._active and self._active.id == goal_id:
                    self._active = None
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