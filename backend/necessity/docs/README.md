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

---

## 与 Aurora 的关系

**这不是一个独立系统，是 Aurora 的质量保障层。** 它通过钩子挂载：

```python
# 默认关闭；设 AURORA_NECESSITY=1 启用
from backend.necessity import adapter
```

挂载点在 `INTEGRATION.md` §4，宿主侧改动在 `backend/agent/graph.py`
（包在 handler 外层，不改 `ToolRegistry.execute`）。
