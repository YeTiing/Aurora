# Aurora Prompt 模板系统 — 39条可复用模板
from __future__ import annotations
import json, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class PromptTemplate:
    name: str
    description: str
    category: str  # coding, review, debug, refactor, doc, test, deploy, general
    template: str
    variables: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def render(self, **kwargs) -> str:
        text = self.template
        for var in self.variables:
            text = text.replace(f"{{{{{var}}}}}", str(kwargs.get(var, f"{{{{{var}}}}}")))
        return text

# ── 内置模板库 ──
BUILTIN_TEMPLATES: list[PromptTemplate] = [
    # — coding —
    PromptTemplate("new-feature", "实现新功能", "coding",
        "请实现以下功能：{{description}}\n\n要求：\n- 遵循现有代码风格\n- 添加类型注解\n- 处理边界情况\n- 添加必要的错误处理",
        ["description"], ["feature", "implement"]),
    PromptTemplate("fix-bug", "修复Bug", "coding",
        "修复以下Bug：{{bug_description}}\n\n相关文件：{{file_path}}\n错误信息：{{error_message}}\n\n先定位根因，再修复。不要引入新问题。",
        ["bug_description", "file_path", "error_message"], ["bug", "fix"]),
    PromptTemplate("add-tests", "添加测试", "test",
        "为以下代码添加单元测试：\n{{code}}\n\n要求：\n- 覆盖正常路径和边界\n- 覆盖错误路径\n- 使用项目现有测试框架",
        ["code"], ["test", "unit-test"]),
    PromptTemplate("refactor-function", "重构函数", "refactor",
        "重构以下函数，提升可读性和可维护性：\n```\n{{function_code}}\n```\n\n要求：\n- 不改变外部行为\n- 提取合理的子函数\n- 改进命名",
        ["function_code"], ["refactor", "cleanup"]),
    PromptTemplate("code-review", "代码审查", "review",
        "审查以下代码，给出改进建议：\n```{{language}}\n{{code}}\n```\n\n检查点：\n- 逻辑正确性\n- 安全性\n- 性能\n- 可读性\n- 错误处理",
        ["code", "language"], ["review", "audit"]),
    PromptTemplate("optimize-performance", "性能优化", "refactor",
        "分析并优化以下代码的性能：\n```{{language}}\n{{code}}\n```\n\n重点关注：时间复杂度、内存使用、IO操作、缓存策略",
        ["code", "language"], ["performance", "optimize"]),
    PromptTemplate("add-error-handling", "添加错误处理", "coding",
        "为以下代码添加完善的错误处理：\n```\n{{code}}\n```\n\n要求：\n- 捕获具体异常类型\n- 提供有用的错误信息\n- 不吞掉错误\n- 适当的重试逻辑",
        ["code"], ["error-handling"]),
    PromptTemplate("write-docstring", "写文档注释", "doc",
        "为以下代码添加文档注释：\n```{{language}}\n{{code}}\n```\n\n要求：参数说明、返回值、异常、使用示例",
        ["code", "language"], ["docstring", "documentation"]),
    PromptTemplate("generate-readme", "生成README", "doc",
        "为项目 {{project_name}} 生成 README.md。\n\n项目描述：{{description}}\n主要功能：{{features}}\n技术栈：{{tech_stack}}",
        ["project_name", "description", "features", "tech_stack"], ["readme", "documentation"]),
    PromptTemplate("api-endpoint", "创建API端点", "coding",
        "创建 API 端点：\n方法：{{method}}\n路径：{{path}}\n描述：{{description}}\n\n要求：\n- 输入验证\n- 错误响应格式统一\n- 适当的HTTP状态码",
        ["method", "path", "description"], ["api", "endpoint"]),
    PromptTemplate("database-migration", "数据库迁移", "coding",
        "为以下数据模型变更创建数据库迁移：\n表名：{{table_name}}\n变更类型：{{change_type}}\n详细描述：{{description}}",
        ["table_name", "change_type", "description"], ["database", "migration"]),
    PromptTemplate("docker-setup", "Docker化", "deploy",
        "为项目 {{project_name}} 创建 Dockerfile 和 docker-compose.yml。\n技术栈：{{tech_stack}}\n端口：{{ports}}\n依赖服务：{{services}}",
        ["project_name", "tech_stack", "ports", "services"], ["docker", "deploy"]),
    PromptTemplate("explain-code", "解释代码", "general",
        "详细解释以下代码的功能和实现思路：\n```{{language}}\n{{code}}\n```",
        ["code", "language"], ["explain", "understand"]),
    PromptTemplate("security-audit", "安全审计", "review",
        "对以下代码进行安全审计：\n```{{language}}\n{{code}}\n```\n\n检查：注入攻击、XSS、CSRF、敏感信息泄露、权限控制、依赖安全",
        ["code", "language"], ["security", "audit"]),
    PromptTemplate("log-analysis", "日志分析", "debug",
        "分析以下日志，找出问题根因：\n```\n{{logs}}\n```\n\n请按时间线梳理问题发生的完整链路。",
        ["logs"], ["log", "debug", "troubleshoot"]),
    PromptTemplate("config-setup", "配置搭建", "general",
        "为项目搭建配置系统。\n需求：{{requirements}}\n\n要求：环境变量、配置文件、默认值、校验、多环境支持",
        ["requirements"], ["config", "setup"]),
    PromptTemplate("dependency-check", "依赖检查", "review",
        "审查以下依赖列表，检查是否有已知漏洞或不推荐的版本：\n```\n{{dependencies}}\n```",
        ["dependencies"], ["dependencies", "security"]),
    PromptTemplate("git-commit-msg", "Git提交信息", "general",
        "根据以下改动生成规范的 git commit message：\n变更内容：{{changes}}\n遵循 conventional commits 规范。",
        ["changes"], ["git", "commit"]),
    PromptTemplate("project-scaffold", "项目脚手架", "general",
        "创建 {{project_type}} 项目脚手架。\n项目名：{{project_name}}\n技术选型：{{tech_stack}}\n\n要求：项目结构、配置、入口文件、开发/构建脚本",
        ["project_type", "project_name", "tech_stack"], ["scaffold", "init"]),
    
    # — 第二批模板 (19条补充，总计39条) —
    PromptTemplate("debug-stacktrace", "分析堆栈跟踪", "debug",
        "分析以下堆栈跟踪并定位问题：\n```\n{{stacktrace}}\n```\n\n请：1) 定位崩溃点 2) 分析调用链 3) 给出修复方案",
        ["stacktrace"], ["debug","stacktrace","crash"]),
    PromptTemplate("design-pattern", "推荐设计模式", "general",
        "为以下场景推荐合适的设计模式：\n场景：{{scenario}}\n语言：{{language}}\n约束：{{constraints}}\n\n说明为什么选择这个模式，并给出示例代码。",
        ["scenario","language","constraints"], ["design","architecture"]),
    PromptTemplate("api-client", "生成API客户端", "coding",
        "为 {{api_name}} API 生成客户端代码。\n语言：{{language}}\n基础URL：{{base_url}}\n认证方式：{{auth_method}}\n端点列表：{{endpoints}}",
        ["api_name","language","base_url","auth_method","endpoints"], ["api","client"]),
    PromptTemplate("data-validation", "数据校验", "coding",
        "为以下数据结构添加输入校验：\n```{{language}}\n{{data_structure}}\n```\n\n校验规则：{{rules}}",
        ["language","data_structure","rules"], ["validation","input"]),
    PromptTemplate("state-machine", "状态机实现", "coding",
        "实现一个状态机管理 {{state_name}} 的状态转换。\n状态列表：{{states}}\n转换规则：{{transitions}}\n初始状态：{{initial}}",
        ["state_name","states","transitions","initial"], ["state-machine","fsm"]),
    PromptTemplate("concurrent-code", "并发编程", "coding",
        "实现以下并发场景：\n{{description}}\n\n要求：线程安全、避免死锁、合适的同步原语",
        ["description"], ["concurrency","async","threading"]),
    PromptTemplate("cli-tool", "CLI工具", "coding",
        "创建一个命令行工具：\n工具名：{{name}}\n功能：{{description}}\n参数：{{arguments}}\n\n使用 {{language}} 实现，支持 --help 和错误处理。",
        ["name","description","arguments","language"], ["cli","tool"]),
    PromptTemplate("env-setup", "环境搭建", "general",
        "为以下项目搭建开发环境：\n项目类型：{{project_type}}\n依赖：{{dependencies}}\n操作系统：{{os}}\n\n提供完整的安装步骤和验证方法。",
        ["project_type","dependencies","os"], ["setup","environment"]),
    PromptTemplate("ci-cd-pipeline", "CI/CD流水线", "deploy",
        "为项目 {{project_name}} 创建 CI/CD 流水线配置。\n平台：{{platform}}\n构建步骤：{{build_steps}}\n部署目标：{{deploy_target}}",
        ["project_name","platform","build_steps","deploy_target"], ["ci","cd","pipeline"]),
    PromptTemplate("monitoring-setup", "监控搭建", "deploy",
        "为服务搭建监控体系。\n服务：{{service_name}}\n需要监控的指标：{{metrics}}\n告警规则：{{alert_rules}}\n工具链：{{toolchain}}",
        ["service_name","metrics","alert_rules","toolchain"], ["monitoring","observability"]),
    PromptTemplate("schema-design", "数据库表设计", "coding",
        "设计 {{table_count}} 张数据库表来存储 {{domain}} 的数据。\n关系：{{relationships}}\n数据库类型：{{db_type}}\n\n包含字段定义、索引和约束。",
        ["table_count","domain","relationships","db_type"], ["database","schema","ddl"]),
    PromptTemplate("query-optimize", "SQL查询优化", "refactor",
        "优化以下SQL查询：\n```sql\n{{query}}\n```\n\n分析执行计划，建议索引和改进方案。",
        ["query"], ["sql","optimize","database"]),
    PromptTemplate("cache-strategy", "缓存策略", "refactor",
        "为 {{scenario}} 场景设计缓存策略。\n数据特征：{{data_characteristics}}\n一致性要求：{{consistency}}\n访问模式：{{access_pattern}}",
        ["scenario","data_characteristics","consistency","access_pattern"], ["cache","performance"]),
    PromptTemplate("error-messages", "错误信息优化", "refactor",
        "改进以下代码的错误信息，使其对用户友好且可操作：\n```{{language}}\n{{code}}\n```\n\n每个错误应包含：发生了什么、为什么、怎么修。",
        ["language","code"], ["error","ux"]),
    PromptTemplate("breaking-change", "破坏性变更迁移", "refactor",
        "将代码从 {{from_version}} 迁移到 {{to_version}}。\n变更内容：{{changes}}\n当前代码：\n```\n{{code}}\n```",
        ["from_version","to_version","changes","code"], ["migration","upgrade"]),
    PromptTemplate("hotfix-guide", "热修复指南", "debug",
        "为以下生产问题制定热修复方案：\n问题：{{problem}}\n影响范围：{{scope}}\n紧急程度：{{severity}}\n\n提供最短路径的修复和回滚方案。",
        ["problem","scope","severity"], ["hotfix","incident"]),
    PromptTemplate("load-test-plan", "压力测试计划", "test",
        "为 {{service_name}} 制定压力测试计划。\n目标QPS：{{target_qps}}\n关键接口：{{endpoints}}\n预期瓶颈：{{bottlenecks}}",
        ["service_name","target_qps","endpoints","bottlenecks"], ["load-test","performance"]),
    PromptTemplate("pair-programming", "结对编程会话", "general",
        "让我们以结对编程的方式解决：{{problem}}\n\n我先提出方案，你审查并改进。然后我们一起迭代实现。",
        ["problem"], ["pair","collaboration"]),
    PromptTemplate("root-cause-analysis", "根因分析", "debug",
        "对以下问题进行 5-Why 根因分析：\n问题描述：{{problem}}\n发生时间：{{when}}\n相关系统：{{systems}}\n已知症状：{{symptoms}}",
        ["problem","when","systems","symptoms"], ["rca","troubleshoot"]),
    PromptTemplate("regex-helper", "正则表达式", "general",
        "写一个正则表达式来匹配：{{pattern_description}}\n\n测试用例：{{test_cases}}",
        ["pattern_description", "test_cases"], ["regex"]),
]

class PromptManager:
    """Prompt 模板管理器 — 内置39条 + 自定义加载"""

    def __init__(self, custom_dir: str | None = None):
        self.templates: dict[str, PromptTemplate] = {}
        for t in BUILTIN_TEMPLATES:
            self.templates[t.name] = t
        if custom_dir:
            self.load_custom(custom_dir)

    def load_custom(self, directory: str | Path):
        d = Path(directory)
        if not d.exists(): return
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text("utf-8"))
                if isinstance(data, list):
                    for item in data:
                        t = PromptTemplate(**item)
                        self.templates[t.name] = t
                elif isinstance(data, dict):
                    t = PromptTemplate(**data)
                    self.templates[t.name] = t
            except: pass

    def get(self, name: str) -> PromptTemplate | None:
        return self.templates.get(name)

    def search(self, keyword: str) -> list[PromptTemplate]:
        kw = keyword.lower()
        results = []
        for t in self.templates.values():
            if kw in t.name.lower() or kw in t.description.lower() or any(kw in tag.lower() for tag in t.tags):
                results.append(t)
        return results

    def by_category(self, category: str) -> list[PromptTemplate]:
        return [t for t in self.templates.values() if t.category == category]

    def list_all(self) -> list[dict]:
        return [{"name": t.name, "description": t.description, "category": t.category, "tags": t.tags, "variables": t.variables} for t in self.templates.values()]

    def render(self, name: str, **kwargs) -> str:
        t = self.get(name)
        if not t: return f"Template '{name}' not found"
        return t.render(**kwargs)

    def auto_select(self, user_input: str) -> PromptTemplate | None:
        """根据用户输入自动匹配最合适的模板"""
        inp = user_input.lower()
        # 关键词匹配
        keyword_map = {
            ("bug","修","fix","错误","异常","crash"): "fix-bug",
            ("test","测试","用例"): "add-tests",
            ("refactor","重构","整理","优化结构"): "refactor-function",
            ("review","审查","检查代码","审计"): "code-review",
            ("性能","慢","优化速度","卡"): "optimize-performance",
            ("doc","文档","注释","readme"): "write-docstring",
            ("api","接口","端点","endpoint","路由"): "api-endpoint",
            ("docker","容器","部署"): "docker-setup",
            ("安全","漏洞","注入","xss"): "security-audit",
            ("log","日志","排查"): "log-analysis",
            ("migration","迁移","ddl","schema"): "database-migration",
            ("readme","项目说明"): "generate-readme",
            ("error","异常处理","try"): "add-error-handling",
            ("正则","regex","pattern"): "regex-helper",
            ("git","commit","提交"): "git-commit-msg",
            ("脚手架","初始化","新建项目","scaffold"): "project-scaffold",
        }
        for keywords, template_name in keyword_map.items():
            if any(kw in inp for kw in keywords):
                return self.get(template_name)
        return None

prompt_manager = PromptManager()