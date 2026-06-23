# Aurora 技能系统 v2 — agents/references/scripts/assets 子目录
"""兼容 Codex 的 SKILL.md + 资源子目录"""
from __future__ import annotations
import yaml, re, hashlib, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class SkillResource:
    name: str
    path: str
    type: str  # agent, reference, script, asset
    content: str = ""
    metadata: dict = field(default_factory=dict)

@dataclass
class Skill:
    name: str
    description: str
    prompt: str
    triggers: list[str] = field(default_factory=list)
    scope: str = "project"
    category: str = "general"
    priority: int = 0
    enabled: bool = True
    file_path: str = ""
    # 子资源
    agents: list[SkillResource] = field(default_factory=list)
    references: list[SkillResource] = field(default_factory=list)
    scripts: list[SkillResource] = field(default_factory=list)
    assets: list[SkillResource] = field(default_factory=list)

    def to_system_prompt(self, include_references: list[str] | None = None) -> str:
        parts = [f'<skill name="{self.name}" description="{self.description}">', self.prompt]

        if include_references:
            for ref_name in include_references:
                for ref in self.references:
                    if ref.name == ref_name:
                        parts.append(f"\n<!-- reference: {ref.name} -->\n{ref.content}")

        parts.append("</skill>")
        return "\n".join(parts)

class SkillManager:
    """技能管理器 v2"""

    def __init__(self, skill_roots: list[str] | None = None):
        self.skill_roots = skill_roots or ["./skills", "~/.aurora/skills"]
        self.skills: dict[str, Skill] = {}
        self._file_hashes: dict[str, str] = {}
        self._scan()

    def _scan(self):
        for root in self.skill_roots:
            root_path = Path(root).expanduser().resolve()
            if not root_path.exists():
                continue
            for md_file in root_path.rglob("SKILL.md"):
                skill_dir = md_file.parent
                try:
                    skill = self._parse_skill(md_file, skill_dir)
                    if skill and skill.name:
                        self.skills[skill.name] = skill
                except Exception:
                    pass

    def _parse_skill(self, md_file: Path, skill_dir: Path) -> Skill | None:
        content = md_file.read_text("utf-8", errors="replace")
        h = hashlib.md5(content.encode()).hexdigest()
        existing = self.skills.get(skill_dir.name)
        if existing and self._file_hashes.get(str(md_file)) == h:
            return existing

        self._file_hashes[str(md_file)] = h

        frontmatter = {}
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except Exception:
                    pass
                body = parts[2]

        name = frontmatter.get("name", skill_dir.name)
        desc = frontmatter.get("description", "")
        triggers = frontmatter.get("triggers", [])
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",")]

        skill = Skill(
            name=name, description=desc, prompt=body.strip(),
            triggers=triggers,
            scope=frontmatter.get("scope", "project"),
            category=frontmatter.get("category", "general"),
            priority=frontmatter.get("priority", 0),
            file_path=str(md_file),
        )

        # 扫描子目录
        skill.agents = self._scan_resource_dir(skill_dir / "agents", "agent")
        skill.references = self._scan_resource_dir(skill_dir / "references", "reference")
        skill.scripts = self._scan_resource_dir(skill_dir / "scripts", "script")
        skill.assets = self._scan_resource_dir(skill_dir / "assets", "asset")

        return skill

    def _scan_resource_dir(self, dir_path: Path, res_type: str) -> list[SkillResource]:
        if not dir_path.exists():
            return []
        resources = []
        for f in dir_path.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                try:
                    content = f.read_text("utf-8", errors="replace")
                except Exception:
                    content = ""
                resources.append(SkillResource(
                    name=f.relative_to(dir_path).as_posix(),
                    path=str(f),
                    type=res_type,
                    content=content,
                ))
        return resources

    def reload(self):
        self.skills.clear()
        self._file_hashes.clear()
        self._scan()

    def add_root(self, path: str):
        if path not in self.skill_roots:
            self.skill_roots.append(path)
            self._scan()

    def match(self, user_input: str, context: dict | None = None) -> list[Skill]:
        matched = []
        inp = user_input.lower()
        for s in self.skills.values():
            if not s.enabled:
                continue
            if f"${s.name}" in user_input:
                matched.append(s)
                continue
            for t in s.triggers:
                if t.lower() in inp:
                    matched.append(s)
                    break
            if context:
                lang = context.get("language", "").lower()
                task = context.get("task", "").lower()
                if lang and lang in s.triggers:
                    matched.append(s)
                if task and any(t.lower() in task for t in s.triggers):
                    matched.append(s)

        matched.sort(key=lambda s: -s.priority)
        seen = set()
        result = []
        for s in matched:
            if s.name not in seen:
                seen.add(s.name)
                result.append(s)
        return result

    def inject(self, skills: list[Skill], include_refs: list[str] | None = None) -> str:
        if not skills:
            return ""
        parts = ["<skills_context>"]
        for s in skills:
            parts.append(s.to_system_prompt(include_refs))
        parts.append("</skills_context>")
        return "\n".join(parts)

    def get_resource(self, skill_name: str, resource_name: str, res_type: str) -> str | None:
        skill = self.skills.get(skill_name)
        if not skill:
            return None
        collection = getattr(skill, f"{res_type}s", [])
        for r in collection:
            if r.name == resource_name:
                return r.content
        return None

    def list_all(self) -> list[dict]:
        return [{
            "name": s.name, "description": s.description,
            "scope": s.scope, "triggers": s.triggers,
            "category": s.category, "file": s.file_path,
            "agents_count": len(s.agents),
            "references_count": len(s.references),
            "scripts_count": len(s.scripts),
        } for s in self.skills.values()]

skill_manager = SkillManager()