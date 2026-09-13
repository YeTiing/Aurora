# INTEGRATION —— 宿主集成层设计

> 解决三个空白：**改造对象是谁**、**四个能力怎么挂上去**、**钩子接口长什么样**

---

## 0. 一句话

> 四个能力不是四个独立运行时，而是**一个核心库 + 一层宿主适配**。
> 核心库与宿主无关；适配层负责把它接到具体 Agent 上。

---

## 1. 改造对象决策

### 1.1 结论

**宿主 = Aurora**（`D:\codex_Projects\Aurora`）

理由：

| 理由 | 说明 |
|---|---|
| 已有 LSP 代码 | `backend/lsp/` 约 1260 行，可直接移植（`INDEX.md` §2） |
| 已有结构化事件 | `agent/sse_events.py` 62 个事件常量，是轨迹采集的现成来源 |
| 已有上下文压缩器 | `context/context_manager.py`，是 Context Paging 的集成点 |
| 已有工具注册表 | `tools/base.py` 的 `ToolRegistry`，是 Guard 的拦截点 |
| 是自有项目 | 可以任意改造，无外部约束 |

### 1.2 改造方式：**非侵入式中间件**

**原则：Aurora 的主循环逻辑尽量不动。** 所有能力通过钩子挂载。

理由：
- 可开关，出问题一键关闭
- 便于把核心库抽出来复用到其他 Agent
- 不在主循环里堆业务逻辑

### 1.3 一个战略补充：为 MCP 化留口

**不是所有能力都适合做成 MCP**，必须诚实区分：

| 能力 | 能否 MCP 化 | 原因 |
|---|---|---|
| **Diff Reducer** | ✅ 完全可以 | 离线、只读、输入是 diff + 仓库路径 |
| **Failure Attribution** | ✅ 完全可以 | 离线分析 |
| **Context Paging** | ⚠️ 部分可以 | 需要宿主**不提供**原生 `read_file`，改用 MCP 提供的读写工具；依赖 Agent 配合 |
| **Constraint Guard** | ❌ **不能** | 必须拦截**所有**工具调用 + 扫描工作区，MCP 协议做不到"拦截别人" |

**因此架构必须分层**，不能假设"全都做成 MCP"：

```
┌───────────────────────────────────────────────┐
│  core/          能力实现（与宿主无关，纯逻辑）    │
└───────────────────────────────────────────────┘
        │                    │              │
        ▼                    ▼              ▼
┌──────────────┐   ┌──────────────┐  ┌──────────────┐
│ adapter/     │   │ mcp/         │  │ cli/         │
│ Aurora 集成   │   │ MCP Server   │  │ 离线工具      │
│ （全能力）     │   │（Reduce/     │  │（Reduce/     │
│              │   │  Attribution │  │  Attribution│
│              │   │  /Context）   │  │  /Context）  │
└──────────────┘   └──────────────┘  └──────────────┘
```

**收益**：Diff Reducer 和 Attribution 以后能挂到 Claude Code / Cursor 上——**产物从"改过的 Agent"变成"可复用的能力层"**。

---

## 2. 目录结构（权威版本）

> **本节是唯一权威。** 其他文档中的模块名以本节为准。

```
backend/necessity/            # Necessity 已合并进 Aurora（见 §1.1）
├── docs/                       # 本目录：设计文档
│   ├── SYSTEM.md               #   总览（入口）
│   ├── INTEGRATION.md          #   本文档
│   ├── EVAL.md                 #   评测设计（权威）
│   ├── INDEX.md                #   基础设施设计
│   ├── CONTEXT_PAGING.md       #   能力 1
│   ├── GUARD.md                #   能力 2
│   ├── DIFF_REDUCER.md         #   能力 3
│   └── ATTRIBUTION.md          #   能力 4
│
├── hooks.py                    # 钩子契约（NecessityHooks Protocol + 数据类型）
├── capability.py               # 能力装配（CompositeHooks + load_capabilities）
├── adapter.py                  # 宿主接入面（原 adapter/aurora/hooks.py + mount.py）
│
├── index/                      # 基础设施：符号 / 调用图 / 影响面
│   ├── symbols.py              #   documentSymbol → 符号表（采点规则见其 docstring）
│   ├── callgraph.py            #   callHierarchy → 调用图
│   ├── impact.py               #   影响面查询 + 测试反查
│   ├── store.py                #   SQLite：symbols / edges / file_content
│   ├── store_schema.py         #   DDL 与路径/哈希/ID 规范化
│   ├── store_ops.py            #   原子重建、文件内容、读取日志
│   ├── store_trace.py          #   agent_event 轨迹表
│   ├── trace.py                #   轨迹采集（只记事实）
│   ├── builder.py              #   遍历仓库 → 采符号 → 查关系 → 写图
│   └── lookup.py               #   按位置反查符号
│
├── context/                    # 能力 1：Context Paging
│   ├── state.py            ├── recall.py            └── compaction.py
│
├── guard/                      # 能力 2：Constraint Guard
│   ├── spec.py  compiler.py  heuristics.py  checker.py  checks.py
│   ├── graph_checks.py  workspace.py  intent.py  rollback.py  feedback.py
│   └── interceptor.py
│
├── reduce/                     # 能力 3：Diff Reducer
│   ├── split.py（切分）  groups.py（一致性组）  apply.py（正/反向应用）
│   ├── search.py（ddmin）  budget.py（预算）  sandbox.py（worktree 隔离）
│   ├── report.py  hooks.py
│
├── attribution/                # 能力 4：Failure Attribution
│   ├── taxonomy.py  signals.py  rules.py  classifier.py
│   ├── meta.py  accuracy.py  hooks.py
│
├── mcp/                        # MCP Server（仅 Reduce / Attribution / Context）
├── cli/                        # 离线命令行
└── eval/                       # 评测
    ├── records.py  task_spec.py      # 数据契约（唯一真源）
    ├── stats.py  effect_size.py      # Wilcoxon / Cliff's delta
    ├── runner.py  plan.py  measure.py  harness.py  agents.py  aurora_agent.py
    ├── report.py  gate0.py
    └── tasks/                        # 任务集（生成器 + 校验器）
        ├── generator.py  traps.py  loader.py
        └── <task-id>/{repo/,task.md,tests/,meta.json}

⚠️ **LSP 传输层不在此包内** —— 统一使用 `backend/lsp/`（Aurora 既有实现）。
   合并时删除了 Necessity 自带的 1273 行 LSP 实现，其修复已移植回
   `backend/lsp/server_manager.py` 与 `server_instance.py`：
     · rootUri / workspaceFolders / processId 填真实值
     · 新增 callHierarchy 三方法 + get_document_symbols
     · get_references 的 includeDeclaration 参数化（默认 False）
     · process_cleanup.py（atexit/signal 兜底，防孤儿进程）
```

**文档与模块的对应**（全部文档已归入 `necessity/` 单一目录）：

| 文档 | 对应模块 | 说明 |
|---|---|---|
| `SYSTEM.md` | — | 系统总览（入口） |
| `INTEGRATION.md` | — | 本文档 |
| `EVAL.md` | `eval/` | 评测设计 |
| `INDEX.md` | `backend/necessity/index/` | 基础设施 |
| `CONTEXT_PAGING.md` | `backend/necessity/context/` | 能力 1 |
| `GUARD.md` | `backend/necessity/guard/` | 能力 2 |
| `DIFF_REDUCER.md` | `backend/necessity/reduce/` | 能力 3 |
| `ATTRIBUTION.md` | `backend/necessity/attribution/` | 能力 4 |

---

## 3. 钩子接口（核心）

### 3.1 接口定义

`core/hooks.py`：

```python
from dataclasses import dataclass
from typing import Literal, Protocol, Any

# ---------- 基础类型 ----------

@dataclass
class ToolCall:
    name: str
    arguments: dict
    turn: int

@dataclass
class ToolResult:
    ok: bool
    output: str
    error: str | None
    duration_ms: float

@dataclass
class Decision:
    """预检结果"""
    action: Literal["allow", "block", "modify"]
    reason: str = ""
    replacement: ToolCall | None = None   # action == "modify" 时使用
    constraint_id: str | None = None      # 违反的约束（用于统计）

@dataclass
class FileChange:
    path: str
    kind: Literal["added", "modified", "deleted"]
    added: int
    removed: int
    by_agent: bool          # 是否 Agent 引起（区分 git/外部改动）

@dataclass
class TaskResult:
    task_id: str
    ok: bool
    turns: int
    tokens: int
    diff_stats: dict

# ---------- 钩子协议 ----------

class NecessityHooks(Protocol):
    # === 生命周期 ===
    def on_task_start(self, task: dict) -> None:
        """任务开始：编译约束、准备 trace、重置会话状态"""

    def on_turn_end(self, turn: int) -> None:
        """每轮结束：Guard 后检、状态更新"""

    def on_task_end(self, result: TaskResult) -> dict:
        """任务结束：产出报告（冗余率 / 约束统计 / 归因）"""

    # === 工具调用 ===
    def before_tool(self, call: ToolCall) -> Decision:
        """预检：可拦截、可改写"""

    def after_tool(self, call: ToolCall, result: ToolResult) -> None:
        """后检 + 轨迹记录"""

    # === 文件读写 ===
    def read_file(self, path: str, opts: dict) -> dict:
        """接管读文件：命中状态表则返回索引，否则真读并建条目"""

    def after_write(self, path: str, writer: str) -> None:
        """写文件后：标记 dirty（writer='agent'）或 stale（其他）"""

    # === 上下文压缩 ===
    def before_compaction(self, messages: list) -> None:
        """压缩前：快照状态表版本（不得被压缩影响）"""

    def after_compaction(self, summary: str) -> str:
        """压缩后：返回增强的 summary（注入 file_state 索引）"""

    # === 工作区 ===
    def scan_workspace(self) -> list[FileChange]:
        """扫描工作区变更（Guard 后检的权威数据源）"""
```

### 3.2 返回 Decision 的语义

| action | 行为 |
|---|---|
| `allow` | 正常执行 |
| `block` | 不执行，把 `reason` 作为工具错误返回给 Agent |
| `modify` | 用 `replacement` 替换原调用后执行 |

**`block` 的 `reason` 会直接进入 Agent 的上下文** —— 这是防约束衰减的反馈通道（见 `GUARD.md` §7.2）。

### 3.3 钩子调用契约

| 规则 | 说明 |
|---|---|
| **钩子抛异常 → 放行 + 记录** | 检查系统故障不得阻塞任务（与 Aurora `approval_gate` 一致） |
| **钩子必须无副作用**（除显式声明） | 只有 `after_write` / `scan_workspace` / `on_task_end` 允许写存储 |
| **钩子不得调用 LLM**（除 `compiler` / `classifier`） | 主循环路径上必须确定性 |
| **超时** | 单钩子 > `hook_timeout_ms`（默认 500）→ 记 warn 并按放行处理 |

---

## 4. 挂载点（Aurora 具体位置）

| 钩子 | Aurora 挂载位置 | 改动方式 |
|---|---|---|
| `on_task_start` | `agent/graph.py` → `run()` / `run_with_stream()` 入口 | 新增一行调用 |
| `before_tool` | `agent/graph.py:828` `handler(name, args, ws)` | 包裹 handler |
| `after_tool` | 同上（handler 返回后） | 包裹 handler |
| `read_file` | `tools/file_rw.py:_handle_read` | 替换实现，走钩子 |
| `after_write` | `tools/file_rw.py` 写入分支 | 新增调用 |
| `on_turn_end` | `agent/graph.py` 每轮循环末尾 | 新增调用 |
| `before_compaction` | `context/context_manager.py:compact()` 入口 | 新增调用 |
| `after_compaction` | 同上，返回前 | 包裹返回值 |
| `on_task_end` | `agent/graph.py` 的 `run()` 返回前 | 新增调用 |
| **轨迹采集** | `agent/sse_events.py` 事件流 | 订阅，不侵入 |

**关键：`before_tool` / `after_tool` 包在 `handler` 外层，而不是改 `ToolRegistry.execute`。**

理由：`ToolRegistry.execute` 是通用基础设施，改它会影响所有调用方；包在 Agent 的 handler 层更聚焦，也更容易开关。

---

## 5. 轨迹采集

### 5.1 现有来源（无需改动）

Aurora `agent/sse_events.py` 已有 62 个事件常量。可直接复用的事件：

| 事件 | 提供的信息 |
|---|---|
| `agent_reasoning_delta` | 思考过程 |
| 工具调用相关事件 | 工具名、参数、结果 |
| `THREAD_FOLLOWER_*` | 线程状态变化 |

### 5.2 需要补的埋点

| 缺什么 | 补在哪 | 用于哪个能力 |
|---|---|---|
| `file_read(path, lines, hash)` | `tools/file_rw.py` | Context Paging、Attribution |
| `file_write(path, added, removed)` | `tools/file_rw.py` | 全部 |
| `compaction(token_before, dropped_range)` | `context/context_manager.py` | Attribution |
| `edit_revert(path, location)` | `agent/graph.py`（回滚分支） | Diff Reducer、Attribution |
| `constraint_violation(...)` | `backend/necessity/guard/interceptor.py` | Guard、Attribution |
| `test_run(suite, result)` | 测试执行处 | Diff Reducer、Attribution |

**埋点原则：只记录事实，不做判断。** 判断留给 `backend/necessity/attribution/signals.py`，这样信号逻辑可以随时改而不影响采集。

### 5.3 轨迹存储

复用 `backend/necessity/index/store.py` 的 SQLite，新增 `agent_event` 表（见 `ATTRIBUTION.md` §7）。

---

## 6. 数据归属（解决重复存储问题）

> **这是原文档的真实冲突：符号索引被两处存储。**

### 6.1 唯一真源原则

```
backend/necessity/index/store.py 的 symbols 表   ← 【唯一真源】
        ▲
        │ 按 (workspace, path, content_hash) 引用
        │
backend/necessity/index/store.py 的 file_content 表   ← 只存内容与哈希，【不存符号】
```

### 6.2 修正后的 schema

```sql
-- 基础设施：符号（唯一真源）
CREATE TABLE symbols (
  id            TEXT PRIMARY KEY,   -- {relpath}::{qualified_name}
  workspace     TEXT NOT NULL,
  file          TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  name          TEXT NOT NULL,
  kind          TEXT NOT NULL,
  start_line    INT, start_col INT,
  end_line      INT, end_col INT,
  signature     TEXT,
  content_hash  TEXT NOT NULL       -- 该符号所属文件当时的哈希
);

-- 基础设施：文件内容（供 Context Paging 使用）
CREATE TABLE file_content (
  workspace     TEXT NOT NULL,
  path          TEXT NOT NULL,
  content       TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  size          INTEGER, mtime REAL,
  token_count   INTEGER,
  indexed_at    REAL,
  PRIMARY KEY (workspace, path)
);

-- Context Paging：会话读取状态
CREATE TABLE file_read_log (
  session_id    TEXT NOT NULL,
  workspace     TEXT NOT NULL,
  path          TEXT NOT NULL,
  read_count    INTEGER DEFAULT 0,
  validity      TEXT NOT NULL,      -- fresh | stale | dirty | unknown
  last_read_at  REAL,
  PRIMARY KEY (session_id, workspace, path)
);
```

### 6.3 符号索引的获取方式

**不再往 `file_content` 里塞 `symbols_json`**，改为：

```sql
SELECT id, name, kind, start_line
FROM symbols
WHERE workspace = ? AND file = ? AND content_hash = ?
```

**关键：用 `content_hash` 作为符号有效性的判据。** 文件内容变了，旧符号自动失效（查不到），不会出现"索引与内容不匹配"。

**这一条同时解决了 Context Paging 的 L1 索引来源问题** —— 不需要额外解析，也不需要重复存储。

### 6.4 原文档需要同步修正的位置

| 文档 | 原写法 | 修正为 |
|---|---|---|
| `CONTEXT_PAGING.md` §6.1 | `file_content.symbols_json` | 删除该列，改查 `symbols` 表 |
| `INDEX.md` §4 | `symbols(id, file, ...)` | 增加 `workspace` 与 `content_hash` |

---

## 7. 权限与安全边界

> **这是原文档完全缺失的一节。**

### 7.1 回滚的安全约束

Guard 的 `rollback` 会写文件，必须限定：

| 约束 | 说明 |
|---|---|
| **只能回滚本任务内 Agent 写过的文件** | 维护任务级写入日志 |
| **回滚前先备份** | 备份到 `.necessity/backup/<session>/<path>` |
| **不得删除用户文件** | 回滚 = 恢复内容，不是删除 |
| **不得触碰工作区外路径** | 路径校验（复用 Aurora 的 `safe_resolve_path`） |
| **回滚失败 → 告警 + 转人工** | 不静默失败 |

### 7.2 worktree 隔离

Diff Reducer 必须用 `git worktree`：

```bash
git worktree add /tmp/necessity-<id> <base-commit>
```

**绝不原地切换**（`git stash` / `git checkout`）—— 会污染用户工作区。

非 git 仓库降级为目录复制，并在报告中标注。

### 7.3 路径校验

所有涉及文件路径的操作，**必须过一遍**：

```python
safe_resolve_path(target, workspace)   # 复用 Aurora tools/base.py
```

> Aurora 这个函数有过一个真实漏洞：早期用 `str.startswith` 判断，`/data/proj-evil` 能逃逸 `/data/proj` 边界。后改为按路径分量判断（`is_relative_to`）。**移植时不要退回旧实现。**

---

## 8. 开关与降级

### 8.1 开关

```json
{
  "necessity": {
    "enabled": true,
    "context_paging": {"enabled": true, "mode": "index"},
    "guard": {"enabled": true, "default_action": "warn"},
    "reduce": {"enabled": false, "trigger": "manual"},
    "attribution": {"enabled": false, "trigger": "offline"}
  }
}
```

**默认策略：**

| 能力 | 默认 | 理由 |
|---|---|---|
| Context Paging | 开 | 风险低，收益直接 |
| Guard | **`warn` 模式** | 先观察违反频率，再决定是否 `rollback` |
| Diff Reducer | 关（手动触发） | 耗时，适合离线跑 |
| Attribution | 关（离线） | 分析用，不在主循环 |

### 8.2 降级矩阵

| 组件失效 | 降级行为 |
|---|---|
| LSP 不可用 | 无符号索引 → Context Paging 只存全文；Guard 不做结构类约束 |
| SQLite 不可写 | Context Paging 关闭（退回现状）；Guard 仍可用（内存态） |
| Guard 检查器异常 | 放行 + 记录 |
| git 不可用 | Diff Reducer 改用目录复制 |

**总原则：任何降级都不得导致 Agent 无法工作。**

---

## 9. 实施顺序（修订版）

> 对应 `SYSTEM.md` §5 的 S1~S7，这里给出**集成视角**的顺序。

| 步骤 | 做什么 | 依赖 |
|---|---|---|
| I0 | 建 `core/` `adapter/` 骨架 + 钩子接口定义（只有接口，无实现） | 无 |
| I1 | 钩子全挂上，**所有实现为空操作**（验证不改行为） | I0 |
| I2 | 埋点：补齐 §5.2 的 6 个事件 | I1 |
| I3 | **P0 基线测量**（见 `EVAL.md` §5 Gate 0） | I2 |
| I4+ | 按 `SYSTEM.md` S1~S7 实现各能力 | I3 |

**I1「空操作挂载」这一步很重要**：先证明钩子层不影响原有行为，再往里填逻辑。否则出问题无法区分是钩子还是能力导致的。

---

## 10. 面试要点

**Q1：为什么做成中间件而不是直接改 Agent？**
→ 三个理由：可开关（出问题一键关）；核心库与宿主解耦（能复用到其他 Agent）；不在主循环堆业务逻辑。

**Q2：哪些能力能做成 MCP？**
→ Diff Reducer 和 Attribution 可以（离线、只读）。Context Paging 部分可以。**Constraint Guard 不行**——MCP 协议做不到"拦截别的工具调用"，它必须宿主集成。**这个区分很重要，不能一概而论。**

**Q3：符号索引存在哪？**
→ 唯一真源在基础设施层。Context Paging 不重复存，按 `(file, content_hash)` 查。**用内容哈希做有效性判据**，文件变了索引自动失效。

**Q4：回滚会不会误删用户文件？**
→ 三条限制：只回滚本任务内 Agent 写过的文件；回滚前备份到 `.necessity/backup/`；回滚是恢复内容不是删除。且所有路径操作过 `safe_resolve_path` 校验。

**Q5：钩子挂了怎么办？**
→ 放行 + 记录。**检查系统故障不得阻塞任务**——这条和 Aurora 已有审批机制的原则一致。

---

## 附：术语表

| 术语 | 含义 |
|---|---|
| **宿主** | 被集成的 Agent（本项目为 Aurora） |
| **钩子（Hook）** | 宿主在关键点回调核心库的接口 |
| **预检 / 后检** | 工具调用前 / 后的检查 |
| **唯一真源** | 同一份数据只存一处，其他位置引用 |
| **空操作挂载** | 钩子全部接上但实现为空，用于验证不改变行为 |
