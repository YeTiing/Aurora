# Aurora 智能体项目 — 企业级差距分析报告

> 分析对象：`D:\codex_Projects\Aurora`（v0.2.0）
> 参照基准：`D:\22ndCentury\Codex\` 下 Codex Desktop 全量逆向报告（ThreadFollower 13 对 API / 77 种 SSE / 9 类审批 / 12 种 Agent 角色 / 8 连接器 / 5 库 19 表 / 35+ 环境变量）
> 分析方法：静态代码审查（169 个后端文件 / 33K 行）+ 全量测试执行 + 启动冒烟验证 + 逐模块闭环追踪
> 分析日期：2026 年

---

## 0. 结论摘要

> **🛠 修复状态更新（2026-08）：个人自用场景下已完成以下修复，全部验证通过（436 tests）。**

| # | 修复项 | 状态 |
|---|--------|------|
| 1 | `safe_resolve_path` 路径穿越漏洞（startswith → is_relative_to） | ✅ `backend/tools/base.py` |
| 2 | `multi_agent._drain_queue` 事件循环 bug（get_running_loop 守卫） | ✅ `backend/multi_agent/__init__.py` |
| 3 | requirements.txt 补 8 个缺失依赖（asyncssh/jsbeautifier/numpy/Pillow/psutil/sentence-transformers/sentry-sdk/pytest） | ✅ `requirements.txt` |
| 4 | 12 个 Agent 角色 TOML 接入（roles_loader + RU(agent_role=) + AgentState.agent_role + chat API 透传） | ✅ 新增 `backend/agent/roles_loader.py` |
| 5 | 多 Agent 工具注册（spawn_agent/send_agent_message/wait_agents/close_agent），子 Agent 用独立 AgentGraph 真实执行 | ✅ 新增 `backend/tools/multi_agent_tools.py` |
| 6 | 审批类型补全（file delete/move/copy、web POST、code_exec、computer_use、mcp_proxy、connector 写操作 + 风险分级细化） | ✅ 新增 `backend/tools/approval_gate.py` |
| 7 | 8 连接器暴露为 Agent 工具（connector_call 白名单 action + 连接状态检查 + 写操作审批） | ✅ 新增 `backend/tools/connector_tools.py` |
| 8 | 回归测试 25 个新增（`tests/test_fixes.py`） | ✅ 全量 436 passed |
| 9 | **推理强度 reasoning_effort 全链路接通**（此前 UI 有选择器但 LLM 调用从未传参）：graph.run/run_with_stream 参数、nodes 三处 LLM 调用、chat API/WS 透传、桌面端 ChatPanel 新增 4 档选择器 + store/hooks/preload/IPC 全链 | ✅ 后端 `graph.py`/`nodes.py`/`chat.py` + 桌面端 5 文件 |
| 10 | 桌面端既有 TS 编译错误修复（`SkinBrowser.tsx` 漏定义 `handlePathBlur`，4 处引用） | ✅ 桌面端 `npm run build` 通过 |

**以下为企业级差距分析正文（M3 企业化项按个人自用场景已搁置）。**

**Aurora 不是 demo，而是一个结构完整、可运行、有真实闭环的 Codex 复刻引擎**：

- ✅ 后端 267 个 API 路由、66 个路由组全部就位，`verify_api.py` 冒烟 **PASSED**
- ✅ 411 个 pytest 测试**全部通过**（README 声称 391，实际更多）
- ✅ 六步 Agent 流水线、LLM 多 Provider、上下文压缩、审批（命令/文件）、Cron 自动化、双记忆、RAG、插件系统、桌面 Electron 端均为**真实实现**
- ✅ 桌面端主进程真实 spawn Python 后端、真实 node-pty 终端、真实 BrowserView CDP 中继、真实 WS 事件桥

**但它仍处于"单机个人工具"成熟度，离"企业级可用"还差三类工作**：

| 类别 | 数量 | 典型问题 |
|------|------|----------|
| 🔴 功能存在但未闭环（半成品） | 10 项 | 12 角色 TOML 无人加载、连接器未进工具环、审批仅 2/9 类型、swarm 子进程假跑 |
| 🟠 安全/健壮性缺陷 | 6 项 | 路径穿越漏洞、workspace-only 沙箱空实现、认证装饰性、依赖缺失 |
| 🔵 企业级能力缺失 | 8 大项 | 无多租户/RBAC、无 CI/CD、无数据库迁移、无可观测性导出、无打包分发 |

---

## 1. 已闭环、真实可用的部分（先肯定现状）

| # | 模块 | 证据（文件:行） | 闭环路径 |
|---|------|----------------|----------|
| 1 | **六步 Agent 流水线** | `backend/agent/graph.py`（959 行）、`nodes.py` | 用户输入 → planner → tool_select → executor → observer → synthesizer → 循环/合成，`run()` 与 `run_with_stream()` 双入口 |
| 2 | **LLM 多 Provider** | `llm_providers.py`（958 行） | OpenAI/Claude/Ollama/OpenRouter/Azure/DeepSeek/Custom，指数退避重试、429/529 分类、流式、tool calling、reasoning effort 透传（llm_providers.py:262） |
| 3 | **Provider 格式互译** | `provider_proxy.py`（20K） | Anthropic Messages ⟷ OpenAI Chat ⟷ Responses 三格式实时翻译（对齐 Codex wire_api） |
| 4 | **上下文压缩** | `context/context_manager.py` | 85% 阈值 → LLM 摘要，保留最近 4 条 + 系统摘要，`compact_async()` 真实调用模型 |
| 5 | **ThreadFollower 13 对 API** | `thread_follower.py`（257 行） | start/steer/interrupt/compact/load-history/edit-last-turn/settings/approval-decision/submit-input/mcp-elicitation/followups 全部实现 + SSE 事件 |
| 6 | **SSE 事件系统** | `agent/sse_events.py` | **62 个事件常量**（对齐报告 77 种的子集），WS `/ws/desktop` 实时转发到桌面端 |
| 7 | **审批（命令/文件）** | `approval.py` + `tools/shell_command.py:63-79` | shell 真实阻塞：assess_risk → request_command_approval → SSE 事件 → WS 决策 → wait_for_decision 放行/拒绝 |
| 8 | **23+ 工具** | `tools/__init__.py` | shell（白名单+分类器+审批）、apply_patch、file_rw、git、code_search、web、browser、MCP proxy、computer_use、memory、cron、skin、LSP、verify_plan 等全部注册 |
| 9 | **SQLite 持久化** | `sqlite_persistence.py`（31K） | 9 张表：threads/thread_goals/agent_jobs/agent_job_items/thread_spawn_edges/logs/memories/state/automation_runs/inbox_items |
| 10 | **双记忆系统** | `dual_memory.py`（40K） | FTS5 会话检索 + 语义记忆 + curator 自动维护 + skills + user profile，`.aurora/` 有真实数据库落盘 |
| 11 | **RAG** | `rag/`（chunker/engine） | tree-sitter AST 分块 + BM25 + 向量 + 重排 |
| 12 | **Cron 自动化** | `cron_scheduler.py` | RRULE 解析 + 自然语言调度 + 60s 后台 ticker + **agent 循环真实消费**（graph.py:220/607 `pop_fires()`） |
| 13 | **Goal/Budget** | `goal.py` | goals.json 持久化、token 预算、3 轮阻塞判定、状态机 |
| 14 | **插件系统** | `plugins/__init__.py` + `plugin_hotreload.py` | Codex `.codex-plugin/plugin.json` 兼容格式、热加载、marketplace、4 个内置插件 |
| 15 | **桌面端 Electron** | `desktop/src/main/index.ts`（815 行） | 真实 spawn 后端、node-pty 终端（带不可用降级）、BrowserView AI 控制（open/navigate/screenshot/click/type/get_html/evaluate）、Tray、通知、preload 全 API |
| 16 | **前端事件闭环** | `renderer/hooks/index.ts` | 将 40+ `codex/event/*` 映射到 store（plan/tool_call/approval/thread_follower 状态） |
| 17 | **测试** | `tests/`（23 文件） | **411 passed / 40.85s**；`verify_api.py` 267 路由全绿 |

> 结论：**核心引擎是"能跑的真东西"**。对照复刻指令的 P0（对话/Composer/SSE/Shell/SystemPrompt/多 Provider）已基本完成；P1（Sidebar/右侧面板/文件操作/审批/设置）完成大半；P2 大多有骨架或半实现。

---

## 2. 🔴 功能存在但未闭环（半成品 / demo 痕迹）

### 2.1 12 个 Agent 角色是死配置 — 从未被加载
- `backend/agent/roles/` 下 12 个 TOML（architect/build-error-resolver/security-reviewer/...）与 Codex 报告的角色一一对应
- **全项目无任何代码读取这些 TOML**（已全局检索：无 load/引用）
- 后果：spawn 带角色的 Agent 不会获得对应角色系统提示词，角色系统形同虚设
- 修复：写 `RoleLoader`，在 spawn/`system_prompt.py` 装配时按角色注入 system prompt

### 2.2 多 Agent 未暴露给 LLM — 编排器是"孤岛"
- `MultiAgentOrchestrator` 只有 REST 路由（`/agents/tree`、`/sessions` stats）可达
- **Agent 工具环里没有 spawn_agent/send_input/wait_agent/close_agent 工具**（Codex 的生命周期 4 连）
- `tools/send_message.py` 是"向用户发消息"，不是"给子 Agent 发消息"
- 后果：LLM 在任务中**无法真正并行拆解子任务**，多 Agent 能力实际用不上
- 修复：注册 `spawn_agent` / `send_agent_message` / `wait_agents` / `close_agent` 工具并接线 orchestrator

### 2.3 多 Agent 编排器有事件循环 Bug（生产必炸）
- `multi_agent/__init__.py:118` 在 `_drain_queue()` 里 `asyncio.create_task(...)`
- `_drain_queue` 会从 `_wrap_run` 的 finally 触发，此时若外层 loop 已关闭 → `RuntimeError: no running event loop`
- **证据：pytest 运行中持续出现 `PytestUnraisableExceptionWarning: coroutine 'MultiAgentOrchestrator._wrap_run' was never awaited`**（tests/test_new_integrations.py 运行输出）
- 修复：`_drain_queue` 内用 `loop = asyncio.get_running_loop()` 守卫，或把 create_task 统一收敛到 `start()` 调度

### 2.4 Swarm 子进程后端是"假跑"
- `swarm/backends.py`：`TerminalBackend.spawn()` 生成的引导脚本**只打印任务描述后 `while True: time.sleep(3600)`**，不执行任何 agent 逻辑
- `send_message` 写 `proc.stdin`，但 win32 走 `cmd /c start` 无 stdin 管道 → **静默 no-op**
- `TMUX` / `REMOTE` 后端在枚举中声明但**无实现**；`multi_agent.spawn()` 里取了 swarm backend 却从未真正调用（死代码）
- 修复：TerminalBackend 改为真正运行 `python -m backend.cli --task ...`，或删掉伪实现并明确标注"仅 UI 占位"

### 2.5 8 个连接器未接入 Agent 工具环 — 只有 OAuth 壳
- `connectors/`：github/gmail/google_calendar/google_drive/linear/notion/slack/figma 的 OAuth + 基础 API 调用是**真的**（github.py 有真实 `/user/repos`、`/search/code` 等）
- 但**没有** `github_search` / `gmail_send` / `notion_query` 之类的工具注册到 `tool_registry`
- 后果：用户能链接账号，但 Agent 全程用不上 → "8 连接器"是数据孤岛
- 修复：每个连接器暴露 2-3 个 ToolSpec（如 `connector_github` / `connector_gmail_send`），工具环统一取 token

### 2.6 审批只有 2/9 类型闭环
- Codex 报告：9 类（command/file_write/file_delete/permission_escalation/network/mcp_tool/browser_action/computer_use/plugin_install）
- Aurora：**command（shell_command.py）与 apply_patch（hooks_system.py:152-163）** 两类可阻塞
- 缺失：file_delete、network、mcp_tool、browser_action、computer_use、plugin_install、permission_escalation 均无审批门
- 后果：`on-request` 模式下删除文件、浏览器敏感操作等仍可绕过审批直跑

### 2.7 审批 UX 断裂 — 聊天窗没有审批按钮
- 前端 `hooks/index.ts` 收到 `exec_approval_request` 后只进 store（`upsertApproval`）
- **ChatPanel.tsx 无任何审批渲染**；只能切到 AdminPanel → approval tab 手动批
- 后果：用户主流程中看不到"Agent 在等审批"，会以为卡死；`wait_for_decision` 30s 超时自动拒绝
- 修复：消息流里内联审批卡片（命令/补丁预览 + 允许/拒绝按钮）

### 2.8 Computer Use 半实现
- `computer_use/engine.py`：screenshot/click/type/scroll/press_key 为真实 pywin32/COM
- **stub**：`set_value`、`perform_secondary_action`、`close`、`end_turn`（engine.py:491-496）→ `lambda p: None`
- 依赖 Windows 专用库，且 `requirements.txt` 未声明（psutil/Pillow 缺失）
- 修复：补全 4 个 stub 或从工具 schema 中移除；加"CU 不可用"降级提示

### 2.9 Hook 系统注册接口是 no-op 占位
- `integrations.py:456` `register_hook` 注册 `lambda ctx: True`（无操作）
- 后果：`/hooks/register` API 看起来可用，实际注册的 hook 不产生任何行为
- 修复：实现 hook 回调持久化 + 调度，或移除该 API 避免误导

### 2.10 "63 种语言"是表面数字
- `backend/i18n/*.json`：63 个文件**每个只有 21 个 key**（仅 nav/按钮级）；渲染器 i18n.ts 有 417 key 但仅中英
- ChatPanel 等组件大量硬编码中文文案
- 修复：前后端统一 key 体系，按需增量翻译

---

## 3. 🟠 安全与健壮性缺陷（上线前必须修）

### 3.1 🔥 路径穿越漏洞：`safe_resolve_path` 用 `startswith` 判断
```python
# tools/base.py:46-52
ws = Path(workspace).resolve()
resolved = (ws / target).resolve()
if not str(resolved).startswith(str(ws)):   # ← BUG
```
- 设 `workspace=/data/proj`，`target=../proj-evil/x` → `resolved=/data/proj-evil/x` → `startswith('/data/proj')` 为 **True** → 放行
- 所有文件读写/补丁工具共享此函数，等于 workspace 边界可被同级目录前缀绕过
- 修复：`resolved == ws or resolved.is_relative_to(ws)`（Python 3.9+），或 `startswith(str(ws) + os.sep)`

### 3.2 `workspace-only` 沙箱是空实现
- `graph.py:835-839`：`if sandbox == "workspace-only" and name in RESTRICTED_TOOLS: pass  # 注释说"工具自己强制"`
- shell_command **没有工作区强制**：cwd=workspace 但 `cd /`、绝对路径、`git -C` 均可逃逸
- `read-only` 模式倒是真的（graph.py:831 直接拦）
- 修复：shell 工具注入 workspace 根并拒绝越界路径参数；或对 workspace-only 做子进程 cwd + 路径参数双重校验

### 3.3 认证是"装饰性"的
- `api/__init__.py:116-124`：中间件只检查**全局单例** `auth_manager.get_active_auth()`，不是每请求鉴权；登录一次后所有请求全放行
- `_AUTH_FREE_PREFIXES` 放过 `/memory /sessions /ws /browser /connectors /settings /config /plugins /agents /skills /models /cron /shared-objects` —— 几乎全放开
- 无 JWT 签发、无 token 头校验、无多用户、无 RBAC、无会话吊销
- 修复：签发 JWT（或 per-request API Key 校验），中间件验签；敏感路由强制鉴权；加角色模型

### 3.4 命令白名单过宽 + 无 OS 级沙箱
- `shell_command.py` 白名单含 `docker / curl / wget / chmod / chown / rm / del` 等高风险命令
- 能拦"非白名单命令"，但**拦不住白名单命令的危险用法**（`rm -rf` 靠 bash_classifier 正则启发式兜底）
- `Dockerfile.sandbox` 存在但**未接线**（没有把命令执行放进容器/受限用户的路径）
- 修复：企业部署必须接 OS 级沙箱（容器/受限账户）；白名单 + 分类器只能做纵深防御第一层

### 3.5 依赖声明不完整（干净环境装不起来）
- `requirements.txt` 缺失：`asyncssh`（remote_control）、`jsbeautifier`（re 反混淆）、`numpy`、`Pillow`、`psutil`、`sentence-transformers`、`sentry-sdk`、`pytest`
- 后果：`remote_control`、语义记忆、CU、Sentry 在全新环境 import 失败 → 功能静默降级
- 修复：补全 requirements（runtime / dev 分开），建议升级到 `pyproject.toml` + uv.lock 锁版本

### 3.6 敏感配置管理
- `.env` / `aurora.json` 直接存 API key；Fernet 密钥落本地文件（有 icacls 权限收紧，起步不错）
- 无 KMS / 密钥轮换 / 最小权限建议
- 修复：企业版支持从环境变量 / Secret Manager / 系统钥匙串读取密钥，配置文件只存引用

---

## 4. 🔵 企业级能力缺失清单（对照 Codex 报告 + 企业标准）

### 4.1 认证 / 多租户 / RBAC（P0）
- 无用户体系、无组织/项目隔离、无 RBAC/ABAC、无 SSO/OIDC、无每请求鉴权（见 3.3）
- 对照：Codex 有 OAuth PKCE + 设备认证 + JWT + MFA；Aurora 只有本地单用户开关

### 4.2 数据持久化与迁移（P0）
- SQLite schema 全部 `CREATE TABLE IF NOT EXISTS` 内联创建，**无版本化迁移**（对照 Codex 5 库 19 表有明确演进）
- 会话消息正文不落库：`graph.run()` 的历史靠前端 `req.history` 传入，重启后对话连续性依赖客户端
- 无备份/恢复、无 WAL 调优、无数据保留/清理策略
- 修复：引入 alembic（或自建 schema_version 表 + 迁移脚本）；会话级消息写入 `messages` 表

### 4.3 可观测性（P1）
- `observability/stats.py` 纯内存（200 样本环形），无 Prometheus/OpenTelemetry 导出
- 日志非结构化（`logging` 直接输出），无 JSON 日志、无 trace_id 贯穿请求→工具→LLM
- Sentry 可选但 DSN 未配置（.env 无 AURORA_SENTRY_DSN）；无成本/用量/计费数据（LLM token 有统计但不出接口）
- 对照：Codex 有 Sentry + Statsig + electron-sampler + file-based-logger + trace-recording-upload

### 4.4 部署与交付（P1）
- **无 CI/CD**（无 .github）、无 Docker 服务端镜像（只有 Dockerfile.sandbox）、无 docker-compose、无 systemd/安装脚本
- 无健康检查/就绪探针（只有 /health）、无优雅停机（lifespan 只归档日志，后台任务/WS 未收尾）
- 桌面端 `out/` 不存在 → 未产出安装包；无代码签名/公证、无自动更新、无崩溃收集配置

### 4.5 高可用与横向扩展（P1）
- 所有状态在进程内：ThreadFollower 线程表、审批 pending、多 Agent DAG、任务队列、限流桶
- 多实例部署会状态分裂；任务无持久化队列（cron fires 在内存）
- 修复：把关键状态下沉 SQLite/Redis（redis_cache.py 已有雏形），任务队列持久化

### 4.6 资源治理与配额（P1）
- 无每会话/每用户的并发、token 成本、磁盘、进程数配额（只有全局 TokenBudget）
- 工具无并发度上限（可同时起 30 个 shell/python 进程）
- 无 LLM 请求的模型路由/降级策略落地（ProviderPool 有池子但 LLMClient 默认不用池）

### 4.7 合规与审计（P2）
- 无不可变审计日志（谁在何时对哪个文件做了什么）——`logs` 表是运行日志不是审计
- 无数据导出/删除（GDPR/个保法）、无内容审查、无 prompt/输出合规策略
- 无依赖 SBOM / 漏洞扫描 / 签名校验

### 4.8 测试与质量工程（P2）
- 411 单测质量不错，但缺：桌面↔后端 E2E、SSE 时序压力测试、并发安全测试（多 Agent 事件循环 bug 正是被并发场景暴露）、模糊测试（命令/路径）、性能基准（长上下文压缩耗时）
- README 声称 391 与实际 411 不一致，需自动化统计

### 4.9 对照 Codex 报告仍缺的功能面
| Codex 能力 | Aurora 现状 |
|-----------|------------|
| Computer Use Named Pipe JSON-RPC（独立 cua_node） | 有 engine 但进程内 + 4 stub |
| Browser Use Chrome 扩展 + CDP 双后端 | 有 BrowserView 中继，无扩展/CDP 双后端 |
| Remote Control（SSH/WSL WebSocket） | remote_control.py 存在但 asyncssh 依赖缺失，未接线验证 |
| Worktree（11 操作） | worktree.py 仅 3.6K，功能面存疑（需核对 11 操作） |
| Connectors 进 Agent 工具环 | 未接入（见 2.5） |
| 多窗口 / PiP / Hotkey Spotlight / 多 Tab | 单窗口，未实现 |
| Auto-dream / 自动化 4 类（schedule/reminder/monitor/follow-up） | 有 cron + auto_dream.py，follow-up/monitor 未闭环 |
| 49KB 完整 System Prompt | 自研 RU/BU 装配器（结构对齐，内容原创，合理） |

---

## 5. 修复优先级路线图（建议）

### 里程碑 M1 — 安全止血（1-2 周，必须）
1. 修 `safe_resolve_path` 路径穿越（3.1）
2. `workspace-only` 沙箱落实（3.2），shell 加工作区强制
3. 补全 requirements.txt 依赖（3.5），干净环境可一键安装
4. 修多 Agent `_drain_queue` 事件循环 bug（2.3）
5. 移除/补全 computer_use stub 与 hooks no-op（2.8 / 2.9）

### 里程碑 M2 — 闭环补全（2-4 周，核心价值）
1. 12 角色 TOML 接入 system prompt（2.1）
2. spawn_agent/send/wait/close 工具注册（2.2）
3. 8 连接器暴露为 Agent 工具（2.5）
4. 审批补齐 7 类缺口 + 聊天窗内联审批 UI（2.6 / 2.7）
5. 会话消息落库 + ThreadFollower 持久化（4.2）

### 里程碑 M3 — 企业化（1-2 月，商用前置）
1. JWT + 每请求鉴权 + RBAC/多租户（4.1）
2. alembic 迁移 + 备份/恢复（4.2）
3. OpenTelemetry/JSON 日志/Sentry 接通（4.3）
4. CI/CD + Docker + 桌面端签名打包 + 自动更新（4.4）
5. 审计日志 + 数据合规（4.7）

---

## 6. 附：本次核验的硬证据

| 验证项 | 结果 |
|--------|------|
| `pytest tests/ -q` | **411 passed**（40.85s），含多 Agent 事件循环 unraisable 警告 |
| `python verify_api.py` | 267 路由 / 66 组全齐 / 17 端点可访问 / **PASSED** |
| 桌面端 dist 新鲜度 | dist 时间戳晚于 src（构建过，未过期）；`out/` 不存在（未打包） |
| 依赖完整性 | requirements.txt 缺 8 个运行时 import（asyncssh/jsbeautifier/numpy/Pillow/psutil/sentence-transformers/sentry-sdk/pytest） |
| 路径穿越 | `safe_resolve_path` 的 `startswith(str(ws))` 可被同级前缀目录绕过（已构造用例论证） |
| 角色系统 | `backend/agent/roles/*.toml` 12 个文件全项目零引用 |
| 连接器进工具环 | `connectors/` 仅被路由引用，无任何 ToolSpec 暴露 |
| 审批类型 | 仅 exec_command / apply_patch 可阻塞，其余 7 类无审批门 |
| i18n | 63 语言 × 21 key；渲染器 417 key 仅中英，组件含硬编码中文 |
