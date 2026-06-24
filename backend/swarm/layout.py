# -*- coding: utf-8 -*-
"Swarm layout manager — visual arrangement of agent terminals."
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass
class LayoutCell:
    agent_id: str = ""
    name: str = ""
    x: int = 0; y: int = 0
    w: int = 80; h: int = 24
    visible: bool = True

class TeammateLayout:
    def __init__(self):
        self._cells = {}
        self._layout = "stacked"
    def add_cell(self, agent_id, name, x=0, y=0, w=80, h=24):
        self._cells[agent_id] = LayoutCell(agent_id=agent_id, name=name, x=x, y=y, w=w, h=h)
    def remove_cell(self, agent_id):
        self._cells.pop(agent_id, None)
    def get_cell(self, agent_id) -> Optional[LayoutCell]:
        return self._cells.get(agent_id)
    def arrange_grid(self, num_agents):
        cols = min(num_agents, 3)
        rows = (num_agents + cols - 1) // cols
        for i, (agent_id, cell) in enumerate(self._cells.items()):
            cell.x = (i % cols) * 100
            cell.y = (i // cols) * 60
    def to_dict(self):
        return {"layout": self._layout, "cells": [c.__dict__ for c in self._cells.values()]}