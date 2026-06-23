# AGENTS.md 规范支持 — 对齐 Codex AGENTS.md spec
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class AgentsMdRule:
    file_path: Path
    scope: Path
    instructions: str
    priority: int = 0

class AgentsMdLoader:
    """扫描并加载 AGENTS.md 文件，注入到 Agent 上下文"""

    def __init__(self):
        self._cache: dict[str, list[AgentsMdRule]] = {}

    def scan(self, workspace: str | Path) -> list[AgentsMdRule]:
        """递归扫描 workspace 内所有 AGENTS.md"""
        root = Path(workspace).resolve()
        cache_key = str(root)
        if cache_key in self._cache:
            return self._cache[cache_key]

        rules = []
        # 扫描根目录
        root_agents = root / "AGENTS.md"
        if root_agents.exists():
            rules.append(AgentsMdRule(
                file_path=root_agents,
                scope=root,
                instructions=root_agents.read_text(encoding="utf-8", errors="ignore"),
                priority=0,
            ))

        # 递归扫描子目录（深度限制4层）
        for agents_file in root.rglob("AGENTS.md"):
            if agents_file == root_agents:
                continue
            if len(agents_file.relative_to(root).parts) > 4:
                continue
            try:
                rules.append(AgentsMdRule(
                    file_path=agents_file,
                    scope=agents_file.parent,
                    instructions=agents_file.read_text(encoding="utf-8", errors="ignore"),
                    priority=len(agents_file.relative_to(root).parts),
                ))
            except Exception:
                pass

        # 按优先级排序（深层覆盖浅层）
        rules.sort(key=lambda r: r.priority)
        self._cache[cache_key] = rules
        return rules

    def get_applicable(self, workspace: str | Path, file_path: str) -> list[AgentsMdRule]:
        """获取适用于特定文件的所有 AGENTS.md 规则"""
        target = Path(file_path).resolve()
        rules = self.scan(workspace)
        return [r for r in rules
                if target.is_relative_to(r.scope) or str(target).startswith(str(r.scope))]

    def inject(self, workspace: str | Path, file_path: str | None = None) -> str:
        """生成要注入到 System Prompt 的 AGENTS.md 上下文"""
        rules = self.scan(workspace)
        if file_path:
            rules = self.get_applicable(workspace, file_path)

        if not rules:
            return ""

        lines = ["# AGENTS.md instructions\n"]
        lines.append("<INSTRUCTIONS>")
        for r in rules:
            lines.append(r.instructions)
        lines.append("</INSTRUCTIONS>")
        return "\n".join(lines)

    def clear_cache(self):
        self._cache.clear()

agents_loader = AgentsMdLoader()