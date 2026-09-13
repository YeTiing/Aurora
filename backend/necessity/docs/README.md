# Necessity —— Agent 改动质量保障系统（设计文档）

> **代码位置**：`backend/necessity/`（本包）
> **宿主**：Aurora（本仓库）—— 见 `INTEGRATION.md` §1.1
>
> Necessity 原为独立项目，按设计文档的原意合并进 Aurora 作为内建模块。
> 本目录是它的完整设计规范。

---

## 一句话

> 现有 Agent 评测只回答「任务完成了吗」——这是**充分性**。
> 本系统回答「完成的过程中，有没有多余的动作」——这是**必要性**。
>
> **充分且必要，才是干净的改动。**

---

## 阅读顺序

1. **`SYSTEM.md`** —— 起点。总览、时序、实施顺序、四个能力为什么是这四个
2. **`INTEGRATION.md`** —— 宿主集成。**目录与模块名的唯一权威**
3. **`EVAL.md`** —— 评测。**指标定义与实验设计的唯一权威**
4. 四个能力的设计文档：
   - `INDEX.md` —— 基础设施（LSP / 符号 / 调用图 / 影响面）
   - `CONTEXT_PAGING.md` —— 能力 1：符号级上下文分页
   - `GUARD.md` —— 能力 2：约束护栏
   - `DIFF_REDUCER.md` —— 能力 3：改动必要性最小化
   - `ATTRIBUTION.md` —— 能力 4：失败根因归因

**冲突处理**：目录/模块名以 `INTEGRATION.md` 为准；指标/实验以 `EVAL.md` 为准。

---

## 四个能力

「必要」不是单一属性，它在任务的不同时刻表现为不同问题：

| 时刻 | 违反「必要」的表现 | 能力 |
|---|---|---|
| 读上下文时 | 反复重读同一文件，浪费上下文 | **Context Paging** |
| 动手之前 | 改动范围超出任务要求 | **Constraint Guard** |
| 动手之后 | diff 里混着多余改动 | **Diff Reducer** |
| 任务失败时 | 只知道挂了，不知道为什么 | **Failure Attribution** |

四个能力覆盖一条时间线，共享一套基于 LSP 的代码结构图。

---

## 文档与代码的对应

| 文档 | 代码 |
|---|---|
| `SYSTEM.md` / `INTEGRATION.md` / `EVAL.md` | 跨模块（契约、装配、评测） |
| `INDEX.md` | `backend/necessity/index/` |
| `CONTEXT_PAGING.md` | `backend/necessity/context/` |
| `GUARD.md` | `backend/necessity/guard/` |
| `DIFF_REDUCER.md` | `backend/necessity/reduce/` |
| `ATTRIBUTION.md` | `backend/necessity/attribution/` |

---

## ⚠️ 合并后的两处与文档不同的地方

1. **LSP 传输层不在本包内** —— 统一使用 `backend/lsp/`。
   合并时删除了 Necessity 自带的 1273 行 LSP 实现，其修复已移植回 Aurora：
   `rootUri`/`workspaceFolders`/`processId`、`callHierarchy` 三方法、
   `get_document_symbols`、`includeDeclaration` 参数化、atexit/signal 兜底。
   详见 `INTEGRATION.md` §2 末尾。

2. **原 `core/` 层级已去掉** —— 直接是 `backend/necessity/{index,context,...}`。
   文档中凡写作 `core/index/...` 之处，现在对应 `backend/necessity/index/...`。

---

## 当前实现状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| I0 | 钩子接口契约 | ✅ |
| I1 | 宿主适配（空操作挂载） | ✅ 挂载前后 Aurora 测试逐字节一致 |
| I2 | 埋点（6 类事件） | ✅ |
| S1 | 基础设施：LSP / 符号 / 调用图 / 影响面 | ✅ 端到端 32 文件 → 159 符号 → 206 边 |
| S2 | 能力 1：Context Paging | ✅ 已实现（效果待评测） |
| S3 | 基础设施：调用图 + 影响面 | ✅ |
| S4 | 能力 3：Diff Reducer | ✅ 已实现（效果待评测） |
| S5 | 能力 2：Constraint Guard | ✅ 已实现（效果待评测） |
| S6 | 能力 4：Failure Attribution | ✅ 已实现（效果待评测） |
| S7 | 端到端评测 | ⏸ **阻塞：需要 LLM API key** |

**评测框架已就绪**（`eval/`：8 对照臂、Wilcoxon 精确检验、Cliff's delta、
任务集生成器 + 校验器），但跑出真实数字需要配置 LLM key ——
`python -m backend.necessity.cli.main eval gate0` 会明确提示缺哪些环境变量，
不会伪造数字。

### 任务集现状（8 个：A2 / B4 / C2）

| 类别 | 数量 | 任务 |
|---|---|---|
| A 基线（grep 也能做对） | 2 | `A-01-rename-func`、`A-02-add-param` |
| B 区分性（同名干扰符号） | 4 | `B-01`~`B-04`（save / reload / describe / flush） |
| C 压力（越界 / 高冗余） | 2 | `C-03-scope-trap`、`C-04-redundancy-trap` |

距离 INDEX.md Phase 3 的 A5/B12/C5 = 22 个仍有差距：**B 类缺 5 个、A 类缺 3 个**。
完整集的主体应从真实 commit 反向构造（`source: "A"`），自造任务只是骨架；
`check_distribution()` 会如实报出这个差距，不假装一致。

**反向前置检查已强制在跑分路径上**（EVAL.md §1.2 第 5 步）：
`EvalRunner.load_tasks()` 默认调用 `tasks/baseline.py`，逐个任务在临时副本里
跑验收测试，**基线必须失败**，否则抛 `TasksetInvalid` 中止整批。
用 `--skip-baseline-check` 可显式跳过（仅供续跑场景）。

> 这条检查一上线就抓出两个真缺陷：`B-02` 声明的干扰符号 `Settings.reload`
> 在仓库里并不存在（干扰不成立 → 任务退化成 A 类），以及两个 A 类任务
> 的基线其实全绿（等于测「Agent 什么都不做」）。两者都不报错，
> 只会让数字虚高 —— 正是它存在的理由。

### 任务快照的 git 元数据不入库（`tasks/repo_git.py`）

任务快照的 `repo/` 是嵌套 git 仓库（Diff Reducer 的 `git worktree` 需要它），
但 git 会把**任何**嵌套仓库记成 gitlink（`mode 160000`）——
只存一个 SHA，克隆出来的 `repo/` 是**空目录，且不报错**。

实测过四种修法，只有一种有效：

| 做法 | 结果 |
|---|---|
| `git add -A` | ❌ 160000 |
| `git add -f`（强加内层文件） | ❌ 160000 |
| 外层 `.gitignore` 排除 `repo/.git/` | ❌ 160000 |
| **内层 `.git` 不入库，克隆后重建** | ✅ 100644，源码入库 |

所以 `repo/.git` 由 `tasks/.gitignore` 排除，`load_all()` 默认调用
`repo_git.ensure_all()` 按需重建初始 commit。重建的代价（新 SHA 而非原 SHA）
写在 `repo_git.py` 的文件头。

> 同一个判据还修掉一个**既有**缺陷：`Sandbox._is_git()` 原先用
> `rev-parse --is-inside-work-tree`，而它对**任何**子目录都返回 true。
> 任务快照恰好嵌在 Aurora 仓库里 —— 于是 worktree 建出来的是
> **Aurora 自己的**（内容里有 Aurora 的顶层文件），Diff Reducer 在分析
> 错对象。改用「`--show-toplevel` 必须等于自己」，并在
> `tests/test_necessity_task_git.py` 里把「为什么不能用旧判据」写成可执行证据。

---

## 与 Aurora 的关系

**这不是一个独立系统，是 Aurora 的质量保障层。** 它通过钩子挂载：

```python
# 默认关闭；设 AURORA_NECESSITY=1 启用
from backend.necessity import adapter
```

挂载点在 `INTEGRATION.md` §4，宿主侧改动在 `backend/agent/graph.py`
（包在 handler 外层，不改 `ToolRegistry.execute`）。
