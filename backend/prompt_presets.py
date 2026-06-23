# Prompt presets — categorized reusable prompt recipes
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PromptPreset:
    id: str
    name: str
    description: str
    prompt_template: str
    category: str
    tags: list[str] = field(default_factory=list)

    def render(self, **kwargs) -> str:
        text = self.prompt_template
        for key, val in kwargs.items():
            text = text.replace("{{" + key + "}}", str(val))
        return text

BUILTIN_PRESETS: list[PromptPreset] = [
    # ── coding ──
    PromptPreset("new-feature", "Implement Feature",
        "Implement a new feature from description",
        "Implement the following feature:\n{{description}}\n\nRequirements:\n- Follow existing code style\n- Add type hints\n- Handle edge cases\n- Add error handling",
        "coding", ["feature", "implement"]),
    PromptPreset("api-endpoint", "Create API Endpoint",
        "Create a REST API endpoint",
        "Create an API endpoint:\nMethod: {{method}}\nPath: {{path}}\nDescription: {{description}}\n\nInclude input validation and proper status codes.",
        "coding", ["api", "endpoint"]),
    PromptPreset("add-error-handling", "Add Error Handling",
        "Add robust error handling to code",
        "Add comprehensive error handling to:\n```\n{{code}}\n```\n\nCatch specific exceptions, provide useful messages, do not suppress errors.",
        "coding", ["error-handling"]),
    PromptPreset("database-migration", "Database Migration",
        "Create a database migration script",
        "Create a database migration for:\nTable: {{table_name}}\nChange: {{change_type}}\nDetails: {{description}}\n\nInclude rollback instructions.",
        "coding", ["database", "migration"]),

    # ── debug ──
    PromptPreset("fix-bug", "Fix Bug",
        "Diagnose and fix a bug",
        "Fix this bug:\n{{bug_description}}\n\nFile: {{file_path}}\nError: {{error_message}}\n\nFind the root cause first, then fix.",
        "debug", ["bug", "fix"]),
    PromptPreset("log-analysis", "Log Analysis",
        "Analyze logs to find root cause",
        "Analyze these logs and find the root cause:\n```\n{{logs}}\n```\n\nTimeline of events and probable cause.",
        "debug", ["logs", "troubleshoot"]),
    PromptPreset("hotfix", "Hotfix Guide",
        "Create an emergency hotfix plan",
        "Production issue: {{problem}}\nImpact: {{scope}}\nSeverity: {{severity}}\n\nProvide minimal fix and rollback plan.",
        "debug", ["hotfix", "incident"]),
    PromptPreset("root-cause", "Root Cause Analysis",
        "5-Why analysis for incidents",
        "Root cause analysis for:\nProblem: {{problem}}\nWhen: {{when}}\nSystems: {{systems}}\nSymptoms: {{symptoms}}\n\nUse 5-Why technique.",
        "debug", ["rca", "analysis"]),

    # ── refactor ──
    PromptPreset("refactor-function", "Refactor Function",
        "Refactor a function for clarity",
        "Refactor this function for readability:\n```\n{{function_code}}\n```\n\nDo not change external behavior. Extract sub-functions and improve naming.",
        "refactor", ["refactor", "cleanup"]),
    PromptPreset("optimize-performance", "Optimize Performance",
        "Analyze and optimize performance",
        "Analyze and optimize performance of:\n```{{language}}\n{{code}}\n```\n\nFocus on: time complexity, memory, I/O, caching.",
        "refactor", ["performance", "optimize"]),
    PromptPreset("simplify-logic", "Simplify Logic",
        "Simplify complex conditional logic",
        "Simplify this complex logic:\n```\n{{code}}\n```\n\nExtract conditions, reduce nesting, improve readability.",
        "refactor", ["simplify", "cleanup"]),

    # ── explain ──
    PromptPreset("explain-code", "Explain Code",
        "Explain how code works in detail",
        "Explain this code:\n```{{language}}\n{{code}}\n```\n\nDescribe the algorithm, data flow, and key design decisions.",
        "explain", ["explain", "understand"]),
    PromptPreset("explain-architecture", "Explain Architecture",
        "Explain system architecture",
        "Explain the architecture of: {{system}}\n\nComponents, data flow, trade-offs, and design patterns used.",
        "explain", ["architecture", "system-design"]),
    PromptPreset("explain-error", "Explain Error Message",
        "Explain an error and how to fix it",
        "Explain this error and how to fix it:\n```\n{{error_message}}\n```\n\nContext: {{context}}",
        "explain", ["error", "troubleshoot"]),

    # ── document ──
    PromptPreset("write-docstring", "Write Docstrings",
        "Add documentation comments",
        "Add docstrings to:\n```{{language}}\n{{code}}\n```\n\nInclude: parameters, return value, exceptions, usage example.",
        "document", ["docstring", "documentation"]),
    PromptPreset("generate-readme", "Generate README",
        "Generate a project README",
        "Generate README.md for {{project_name}}.\nDescription: {{description}}\nFeatures: {{features}}\nTech stack: {{tech_stack}}",
        "document", ["readme", "documentation"]),
    PromptPreset("api-docs", "API Documentation",
        "Generate API documentation",
        "Document this API:\n```\n{{api_code}}\n```\n\nEndpoints, request/response formats, auth, error codes.",
        "document", ["api-docs", "documentation"]),

    # ── test ──
    PromptPreset("unit-tests", "Write Unit Tests",
        "Generate unit tests for code",
        "Write unit tests for:\n```{{language}}\n{{code}}\n```\n\nCover: normal paths, edge cases, error paths. Use the project testing framework.",
        "test", ["unit-test", "testing"]),
    PromptPreset("integration-tests", "Write Integration Tests",
        "Generate integration tests",
        "Write integration tests for:\n{{description}}\n\nComponents: {{components}}\nTest: API calls, database, external services.",
        "test", ["integration", "testing"]),
    PromptPreset("load-test-plan", "Load Test Plan",
        "Create a load testing plan",
        "Create load test plan for {{service_name}}.\nTarget QPS: {{target_qps}}\nEndpoints: {{endpoints}}\nBottlenecks: {{bottlenecks}}",
        "test", ["load-test", "performance"]),
]

class PresetManager:
    """Manage and search prompt presets."""

    def __init__(self):
        self.presets: dict[str, PromptPreset] = {}
        for p in BUILTIN_PRESETS:
            self.presets[p.id] = p

    def list_all(self) -> list[dict]:
        return [{
            "id": p.id, "name": p.name, "description": p.description,
            "category": p.category, "tags": p.tags
        } for p in self.presets.values()]

    def list_by_category(self, category: str) -> list[dict]:
        return [{
            "id": p.id, "name": p.name, "description": p.description,
            "category": p.category, "tags": p.tags
        } for p in self.presets.values() if p.category == category]

    def get(self, preset_id: str) -> PromptPreset | None:
        return self.presets.get(preset_id)

    def search(self, query: str) -> list[dict]:
        q = query.lower()
        results = []
        for p in self.presets.values():
            if q in p.id.lower() or q in p.name.lower() or q in p.description.lower():
                results.append({
                    "id": p.id, "name": p.name, "description": p.description,
                    "category": p.category, "tags": p.tags
                })
            elif any(q in tag.lower() for tag in p.tags):
                results.append({
                    "id": p.id, "name": p.name, "description": p.description,
                    "category": p.category, "tags": p.tags
                })
        return results

    def render(self, preset_id: str, **kwargs) -> str:
        p = self.get(preset_id)
        if not p:
            return f"Preset '{preset_id}' not found"
        return p.render(**kwargs)

    def categories(self) -> list[str]:
        cats = set(p.category for p in self.presets.values())
        return sorted(cats)


preset_manager = PresetManager()
