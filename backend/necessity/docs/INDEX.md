# CodeGraph — 结构感知的编码 Agent

> **所属**：Necessity 系统的 `backend/necessity/index/` 模块（**基础设施**，被其他四个能力共同依赖）
> **入口**：`SYSTEM.md` ｜ **集成**：`INTEGRATION.md` ｜ **评测**：`EVAL.md`
>
> 项目规划 + 分阶段提示词（v2，已根据代码核实结果修订）
> 目标：把"代码关系图"从**给人看的工具**，做成**给 Agent 用的能力**，并用数字证明它有效。

**v2 修订要点**
1. 新增 **Phase 0**（环境与协议探明）——没有它，Phase 1 全是盲写
2. Phase 1 从"从零写 LSP 客户端"改为 **移植 Aurora 已有实现**（省一半工作量 + 避开已知坑）
3. 修正 `rootUri` / `workspaceFolders` 问题（Aurora 原实现缺这两个字段）
4. 验收标准从"准确率 ≥90%"改为 **precision / recall 对**
5. **Phase 3 任务集重新设计**——原设计偏 grep 友好，会导致实验白做
6. 风险表补充：孤儿进程、串行 JSON-RPC 开销、采点位置、自环
7. 明确 symbol id 生成方案（Phase 4 依赖）
8. 修正 `implements` 的说明（LSP 有这个能力，但语义不匹配）

---

## 0. 一句话定位（简历 / 面试用）

> 基于 LSP 构建代码结构图，让编码 Agent 在修改函数前自动获取全部调用方与相关测试，
> **跨文件重构的破坏率从 __% 降至 __%**。

**核心不是那张图，是"Agent 因为有了图，改代码不再改坏"。**

---

## 1. 要解决的问题（面试开场白）

现在编码 Agent 找上下文主要靠 grep + 向量检索。但代码检索的本质问题是：

> **"语义相似" ≠ "结构相关"**

Agent 要改一个方法时：
- ✅ 能搜到"写得像"的其他代码（向量检索的强项）
- ❌ **搜不到调用这个方法的那 5 个地方**——而那 5 个地方才是真正会被改坏的

现有工具的缺口：
| 工具 | 有什么 | 缺什么 |
|---|---|---|
| IntelliJ | Call Hierarchy（给人看） | 不会替 Agent 决定上下文 |
| Sourcegraph | 跨仓库引用 | 同上 |
| 向量 RAG | 语义相似 | 不懂结构 |
| tree-sitter | 语法作用域 | **没有符号绑定**，区分不了同名符号 |
| 大多数编码 Agent | grep + 读文件 | 不知道影响面 |

**IDEA 能做调用层级，但 IDE 不会替 Agent 组装上下文、不会判断"这次改动会炸谁"。这就是缺口。**

---

## 2. 已有的地基（重要：先读这一节再动手）

**`D:\codex_Projects\Aurora\backend\lsp\` 已经有一套可用的 LSP 集成，共约 1260 行：**

| 文件 | 行数 | 提供什么 | CodeGraph 是否复用 |
|---|---|---|---|
| `lsp/client.py` | **331** | JSON-RPC 2.0 + Content-Length 分帧 + stdio + initialize 握手 + pending future + 崩溃回调 + Windows `CREATE_NO_WINDOW` | ✅ **直接移植** |
| `lsp/server_instance.py` | 205 | 进程状态机 + 启动/停止 + 重试 | ✅ 移植，**但必须修 `rootUri`** |
| `lsp/server_manager.py` | 303 | 按文件后缀选 server + hover/definition/references | ✅ 移植，**补 callHierarchy** |
| `lsp/config.py` | 163 | pyright / pylyzer / tsserver / rust-analyzer / gopls 内置配置 | ✅ 直接复用 |
| `lsp/diagnostic_registry.py` | 141 | 诊断收集 | ⭕ 可不移植 |
| `lsp/passive_feedback.py` | 102 | 被动反馈 | ⭕ 可不移植 |
| `tools/lsp_tool.py` | — | 包成 Agent 工具（4 个 action：hover / definition / references / diagnostics） | ✅ 参考其工具封装方式 |

### 2.1 CodeGraph 的真实增量（= 真正要做的工作）

| 需要的能力 | Aurora 现状 |
|---|---|
| `callHierarchy/prepareCallHierarchy` / `incomingCalls` / `outgoingCalls` | ❌ **全项目零处使用** |
| `textDocument/documentSymbol`（采点必需） | ❌ **全项目零处使用** |
| 符号-边图的持久化（SQLite） | ❌ 无 |
| `impact_analysis` / 上下文组装 | ❌ 无 |
| 评测体系 | ❌ 无 |

**结论：CodeGraph 的工作量集中在"图 + 影响面 + 评测"，传输层直接拿来用。**

### 2.2 Aurora 已知缺陷（移植时必须修）

**① `rootUri: None` + `workspaceFolders: []`**（`server_instance.py:113` 和 `:129`）

```python
init_params = {
    "processId": None,          # 第 112 行，注释写 "Not a child of an editor"
    "rootUri": None,            # ← 第 113 行
    ...
    "workspaceFolders": [],     # ← 第 129 行
}
```

**对 CodeGraph 是必须修的**：pyright 靠 `rootUri` / `workspaceFolders` 确定项目根目录，没有它就找不到 `pyrightconfig.json`、无法跨包解析。而 CodeGraph 的全部价值就是**跨文件**调用关系。

（注：Aurora 里 pyright 进程的 `cwd` 被设成了工作目录，可能靠 cwd 兜住了，所以没暴露。但这是隐式依赖，不能靠它。）

**修法：**
```python
repo_root = Path(repo_path).resolve()
init_params = {
    "processId": os.getpid(),                       # 让 server 感知父进程
    "rootUri": repo_root.as_uri(),                  # file:///...
    "workspaceFolders": [{"uri": repo_root.as_uri(), "name": repo_root.name}],
    ...
}
```

**② `includeDeclaration: True`**（`server_manager.py:268`）

会把定义点也当成"引用方"返回，导致图里出现自环。CodeGraph 必须传 `false`。

**③ 缺 `atexit` / signal 兜底**

`client.py:144,148` 在 `stop()` 里有 `terminate()` / `kill()`，正常退出能清理。但主进程被 Ctrl+C 或硬杀时 `stop()` 不会执行，**pyright 会变成孤儿进程堆积**。必须加 `atexit` + `signal` 处理器。

---

## 3. 技术方案

### 3.1 架构

```
仓库文件
   │  全量扫描 / 文件监听
   ▼
LSP 客户端（移植自 Aurora backend/lsp/）
   │  textDocument/documentSymbol        → 采点（拿符号位置）
   │  textDocument/references            → 谁引用了这个符号
   │  callHierarchy/prepareCallHierarchy
   │  callHierarchy/incomingCalls        → 谁调用了这个函数
   │  callHierarchy/outgoingCalls        → 这个函数调用了谁
   ▼
结构图（SQLite）
   ├─ 节点：符号（限定名 + 位置 + 类型 + 签名）
   └─ 边：calls / references / inherits
   │
   ▼
查询接口：impact_analysis(symbol)
   │
   ▼
上下文组装：调用方 + 被调用方 + 相关测试 + token 预算控制
   │
   ▼
Agent（改代码前先拿影响面）
```

### 3.2 Symbol ID 设计（必须现在定，Phase 4 依赖它）

**主键：`{相对路径}::{限定名}`**

例：`src/models/user.py::User.save`

**为什么不用行号：** 行号会随编辑漂移，做增量更新时 id 不稳定。
**为什么用限定名：** 重命名后旧符号自然消失（这是**正确行为**——重命名就是"旧符号没了 + 新符号出现"），其余情况下稳定。

**位置信息（start_line/start_col）作为属性存储，不参与主键。**

**必须处理的边界情况：**
- 嵌套函数 → 限定名要带父级（`outer.inner`）
- 类方法 → `Class.method`
- 重载/同名（Python 少见但存在）→ 追加位置后缀消歧
- 匿名函数 / lambda → 第一版**不建节点**（无法稳定命名）

### 3.3 关键设计决策（面试必问）

| 决策 | 为什么这么做 | 备选方案的缺点 |
|---|---|---|
| **用 LSP，不自写解析器** | 准确性是编译器级的；拿到的是**符号绑定**不是文本 | 自写 parser 只能做名字匹配 |
| **不用 tree-sitter** | tree-sitter 只给语法作用域，**不做符号绑定**——`A.save()` 和 `B.save()` 区分不了 | 而区分同名符号**正是主指标的来源** |
| **图存 SQLite，不上 Neo4j** | 单仓库规模用不上图数据库 | 多一个部署依赖，收益为零 |
| **只做调用/引用/继承，不做数据流** | 控制范围；数据流分析难度高一个数量级 | 会让项目失控 |
| **上下文按"图距离"排序** | 直接调用方远比间接调用方重要 | 无差别塞入浪费 token 预算 |
| **id 用限定名，不用行号** | 增量更新需要跨编辑稳定 | 行号会漂移 |
| **第一版只做 Python** | pyright 最成熟、自己看得懂、调试快 | 多语言会让第一版无限延期 |

### 3.4 明确不做（防止范围爆炸）

- ❌ 不做 Web UI / 可视化界面 —— 那是 IDEA 的地盘，做了不加分
- ❌ 不做数据流 / 污点分析
- ❌ 不做多语言（Java 用 jdtls 可作二期）
- ❌ 不写自己的语法分析器
- ❌ 不引入 Celery / Redis / 消息队列
- ❌ 不做插件系统 / 多后端抽象
- ⚠️ **`implements` 关系不做**——LSP 有 `textDocument/implementation`，但 pyright 返回的是"抽象方法的具体实现"，**语义和 Java 的 implements 不是一回事**，混进图里会脏。第一版只保留 `calls` / `references` / `inherits`

---

## 4. 分阶段计划

### Phase 0（半天）：环境与协议探明 ⭐ 必须做

**为什么必须先做：** 后面所有设计（schema、采点逻辑、边类型）都依赖 LSP 的**真实返回结构**。不看真实 JSON 就写代码 = 盲写。

**任务**
1. 安装 pyright：`npm i -g pyright` 或 `pip install pyright`，确认 `pyright-langserver --stdio` 能启动
2. 手工跑通一条完整链路（**带正确的 rootUri**）：
   ```
   initialize → initialized
   → textDocument/didOpen
   → textDocument/documentSymbol
   → textDocument/references
   → callHierarchy/prepareCallHierarchy
   → callHierarchy/incomingCalls
   ```
3. **把每一步的真实 JSON 返回 dump 到文件存档**（`probe/*.json`）

**必须用真实返回确认的 5 件事**
| # | 要确认什么 | 为什么重要 |
|---|---|---|
| 1 | `documentSymbol` 的 `range` vs `selectionRange` —— 哪个起点才指向**符号名** | 采点错了，references 和 callHierarchy 会返回空列表 |
| 2 | `callHierarchy/incomingCalls` 返回结构（是否含 `fromRanges`、`from` 里有什么） | 决定边怎么存 |
| 3 | `textDocument/references` 传 `includeDeclaration: false` 的效果 | 确认不会出现自环 |
| 4 | `textDocument/implementation` 在 pyright 上返回什么 | 验证"语义不匹配"的判断是否正确 |
| 5 | `initialize` 返回值里的 `capabilities.callHierarchyProvider` 是否为 true | 确认 pyright 版本支持 |

**验收标准**
- [ ] `probe/` 下有 5 个真实 JSON 文件
- [ ] 能用文档写清"采点规则是什么"

---

### Phase 1（第 1 周）：移植 + 补 callHierarchy + 建图

**交付物**

| 模块 | 来源 | 工作内容 |
|---|---|---|
| `lsp/client.py` | **移植 Aurora** | 基本不改，加 atexit/signal 兜底 |
| `lsp/config.py` | **移植 Aurora** | 只留 pyright |
| `lsp/server_instance.py` | **移植 Aurora** | **修 `rootUri` / `workspaceFolders` / `processId`** |
| `lsp/server_manager.py` | **移植 Aurora** | `includeDeclaration` 改 false；**新增 callHierarchy 三个方法** |
| `symbols.py` | **新写** | `documentSymbol` 遍历 + 按 `selectionRange` 采点 + 生成限定名 ID |
| `graph_store.py` | **新写** | SQLite 图存储 |
| `builder.py` | **新写** | 遍历仓库 → 采点 → 查关系 → 写图 |
| `cli.py` | **新写** | `build` / `callers` / `stats` |

**graph_store schema**
```sql
-- ⚠️ 这是符号索引的【唯一真源】。其他模块（如 Context Paging）按
--    (workspace, file, content_hash) 引用，不得重复存储。见 INTEGRATION.md §6。
symbols(
  id TEXT PRIMARY KEY,          -- {workspace}::{relpath}::{qualified_name}
  workspace TEXT NOT NULL,      -- 工作区绝对路径（多工作区隔离）
  file TEXT NOT NULL,           -- 相对路径
  qualified_name TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,           -- function | method | class
  start_line INT, start_col INT,
  end_line INT, end_col INT,
  signature TEXT,
  content_hash TEXT NOT NULL    -- 建索引时该文件的哈希（有效性判据）
);
edges(
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  kind TEXT NOT NULL,           -- calls | references | inherits
  file TEXT, line INT
);
CREATE INDEX idx_edges_src ON edges(src_id);
CREATE INDEX idx_edges_dst ON edges(dst_id);
CREATE INDEX idx_symbols_file ON symbols(workspace, file, content_hash);
```

> **`content_hash` 的作用**：查询符号时带上当前文件哈希。内容变了 → 查不到旧符号 → 自动失效，**不会出现「索引与内容不匹配」**。

**必须处理的坑（Aurora 没遇到，因为它是按需查询）**
- **采点**：用 `selectionRange.start`，不是 `range.start`（后者落在 `def` 行）
- **didOpen 顺序**：必须 `didOpen` → 等 `publishDiagnostics`（或固定 sleep）→ 才能查 references，否则拿到空
- **并发**：100 文件 × N 符号 × 2~3 次请求 = **上万次往返**。串行必然超时。必须批量 `didOpen` 后并发查（`asyncio.gather` 分批，注意 LSP server 的并发上限）

**验收标准（改为可证伪的 P/R）**
- [ ] 拿一个真实 Python 仓库（≥50 文件）跑通 `build`
- [ ] 人工标注 **20 个符号**的真实调用点集合（ground truth）
- [ ] 机器结果 vs 人工标注，计算：
  - **Precision** = 正确调用点 / 机器返回的调用点
  - **Recall** = 正确调用点 / 人工标注的调用点
- [ ] **P ≥ 0.90 且 R ≥ 0.85**
- [ ] 100 文件仓库全量 build < 5 分钟

> 标注时**必须混合选取**：有调用方的符号 + 无调用方的符号 + 同名符号，不能只挑有调用方的。

---

### Phase 2（第 2 周）：Agent 集成

**交付物**
- `impact.py` —— `impact_analysis(symbol_ref, depth=2)`：
  ```
  {
    callers: [{file, line, symbol, distance}],   # distance=1 直接调用方
    callees: [{file, line, symbol, distance}],
    tests:   [{file, test_name}],                # 按命名/目录约定识别
    risk:    "high" | "medium" | "low"           # 基于调用方数量与是否有测试
  }
  ```
- `context_assembler.py` —— 按 token 预算组装，优先级：
  `直接调用方 > 目标符号本身 > 间接调用方 > 相关测试`；超预算时截断并标注"因预算截断 N 项"
- `agent_tool.py` —— 工具 description 必须写：
  > "在修改任何函数签名、移动函数、改变返回值之前调用此工具，获取全部调用方与受影响测试。不要用 grep 代替。"

**验收标准**
- [ ] 给 Agent 任务"把 `foo()` 改名为 `bar()` 并更新所有调用点"
- [ ] Agent 无需 grep 即可拿到完整调用列表
- [ ] 8k token 预算内包含**全部直接调用方**

---

### Phase 3（第 3 周）：评测与消融实验 ⭐ **核心产出**

> ⚠️ **这一阶段的设计 v1 版本是错的**：原来选的任务类型（改签名/改返回值/提取方法/移动函数）**大多是 grep 友好的**——Python 里改个函数名，`grep` 几乎总能找全 95%+ 的调用点。真跑完很可能是"两组持平"，第三周白干。

#### 任务集设计（重新设计）

**必须有意识地分成三类：**

| 类别 | 任务类型 | grep 会怎样 | 用途 |
|---|---|---|---|
| **A. 基线任务**（grep 能做对） | 单文件内改函数名；简单参数增加 | 基本正确 | 证明 graph **不比 grep 差**（防止倒退） |
| **B. 区分性任务** ⭐（grep 天生做不对） | 见下表 | 必然误伤或漏 | **主指标的真正来源** |
| **C. 压力任务**（都难） | 跨模块提取方法、接口重构 | 两组都难 | 看 graph 是否仍有增量 |

**B 类（区分性任务）——这是项目的卖点，至少占一半：**

| 场景 | grep 的行为 | graph 的优势 |
|---|---|---|
| **同名方法混淆**：`User.save()` 和 `Config.save()` 共存，只改其中一个 | 两个全找出来 → **误伤另一个** | LSP 的符号绑定能精确区分 |
| **动态调用** `getattr(obj, "save")()` | **完全找不到** | pyright 部分可推断 |
| **子类覆写 + 基类调用** | 只能找到一半 | callHierarchy 能串起整条链 |
| **装饰器包装** | 找不到真实调用链 | 同上 |
| **需要类型推断才知道的调用** | 找不到 | pyright 能推 |

> **写任务集时的硬性要求：每个 B 类任务，仓库里必须存在一个同名的干扰符号。** 这是整个实验能否成立的关键。

**任务目录结构**
```
tasks/001-same-name-method/
  repo/          起始代码快照
  task.md        重构要求（自然语言，模拟真实需求）
  tests/         验收测试（客观判定依据）
  meta.json      {task_id, category: "A|B|C", difficulty,
                  files_touched, expected_callers, decoy_symbols}
```

数量：A 类 5 个、**B 类 12 个**、C 类 5 个，共 22 个（跑通后再扩）

#### 跑分

`runner.py` 对每个任务：
1. 复制快照到临时目录
2. 用 Agent 执行 `task.md`
3. 跑 `tests/` 判定 pass/fail
4. 记录：是否通过、token、工具调用次数、轮次

两种模式，同一任务各跑一次：
- **baseline** —— Agent 只有文件读写 + grep
- **graph** —— 额外有 `impact_analysis` 工具

#### 指标

| 指标 | 说明 |
|---|---|
| **破坏调用方的次数** ⭐ | 主指标：改动完成后仍存在调用旧签名/旧名字的地方，或验收测试因调用点未更新而失败 |
| 任务通过率 | 总通过率 **+ 按 A/B/C 分类统计** |
| 平均 token / 平均轮次 | 成本与效率 |

> **报告必须分类统计。** 如果只报总通过率，A 类（grep 也能做）会把 B 类的差距稀释掉。**B 类上的差距才是能写进简历的数字。**

#### 验收标准
- [ ] 一条命令跑完全部任务并输出表格
- [ ] **B 类任务上 graph 组在主指标上明显优于 baseline**
- [ ] A 类任务上 graph 组不比 baseline 差（不倒退）
- [ ] 数字可直接写进简历

**明确不做**
- ❌ 不要用 LLM 当裁判（用测试用例判定，客观、可复现）
- ❌ 不要为了指标好看而手工干预结果（**哪怕结论是"没提升"也要如实记录**）
- ❌ 不要在同一模式下混用两种配置

---

### Phase 4（第 4 周，可选）：打磨
- 增量更新（文件变更只重算受影响部分，依赖 §3.2 的稳定 id）
- README + 架构图
- 简历条目 + 面试话术定稿

---

## 5. 风险与退路

| 风险 | 严重度 | 退路 |
|---|---|---|
| **Phase 3 两组持平**（任务集偏 grep 友好） | 🔴 最高 | 必须按 §Phase 3 设计任务集，**B 类占一半以上**；若仍持平，改为报告"graph 在特定场景下的优势"而非总指标 |
| **图不准 → Agent 自信地漏改** | 🔴 高 | Phase 1 必须验到 P≥0.90/R≥0.85；结果带置信度标注；低置信度时不返回而不是返回错的 |
| **采点位置错 → 全部返回空** | 🟠 中 | 用 `selectionRange.start`；Phase 0 先用真实 JSON 确认 |
| **串行 JSON-RPC 打爆时间预算** | 🟠 中 | 批量 didOpen + 并发查询；必要时分片处理 |
| **pyright 孤儿进程堆积** | 🟠 中 | atexit + signal 兜底；启动时先清理同名残留进程 |
| **LSP 启动/索引慢** | 🟡 低 | server 常驻 + 预热；或先用小仓库验证 |
| **pyright 类型推断不全，关系漏抽** | 🟡 低 | 标注置信度；不追求 100% 覆盖，追求"返回的都是对的" |
| **任务集难造** | 🟡 低 | 从开源仓库真实历史 commit 反向构造（找"改签名"类提交） |
| **时间不够** | 🟡 低 | 砍 Phase 4；Phase 3 任务数缩到 A3/B6/C2 |

---

## 6. 提示词

> 用法：把「总纲」贴在每次对话开头，再贴对应 Phase 的任务。**一次只做一个 Phase。**

### 6.1 总纲提示词（每次对话开头都带上）

```
你是一个 Python 项目的实现助手。项目名 CodeGraph。

【项目目标】
构建代码结构图，为编码 Agent 提供"改动影响面"查询能力。
注意：这不是做给人看的可视化工具，是给 Agent 用的结构化检索层。
价值在于让 Agent 改代码前知道"会炸谁"，而不是画一张好看的图。

【已有地基 —— 必须先读】
D:\codex_Projects\Aurora\backend\lsp\ 下已有一套可用的 LSP 集成（约 1260 行）：
  client.py (331行)          JSON-RPC 2.0 + Content-Length 分帧 + stdio + 握手 + 崩溃回调
  server_instance.py (205行) 进程状态机 + 启动停止重试
  server_manager.py (303行)  按后缀选 server + hover/definition/references
  config.py (163行)          pyright 等内置配置
要求：传输层【移植】这些文件，不要重写。先读它们的代码再动手。

【移植时必须修的已知缺陷】
1. server_instance.py:113 `rootUri: None` 和 :129 `workspaceFolders: []`
   → pyright 无法确定项目根目录，跨文件解析会退化。必须设为仓库根目录的 file:// URI。
2. server_instance.py:112 `processId: None`
   → 改为 os.getpid()
3. server_manager.py:268 `includeDeclaration: True`
   → 会导致定义点被当成引用方，图里出现自环。改为 false。
4. 缺 atexit / signal 兜底 → 主进程被硬杀时 pyright 会变孤儿进程

【技术约束】
- 语言：Python 3.11+
- 关系抽取：LSP 协议 + pyright-langserver，不要自己写语法分析器
- 存储：SQLite。不要引入 Neo4j 或任何图数据库
- 接口：先 CLI，Phase 2 再包成 Agent 工具
- 依赖尽量少，能用标准库就用标准库

【严格禁止】
- 不要做 Web UI / 可视化界面
- 不要做数据流分析
- 不要做多语言支持（第一版只做 Python）
- 不要写自己的 parser
- 不要用 tree-sitter 替代 LSP（tree-sitter 没有符号绑定，区分不了同名符号）
- 不要引入 Celery / Redis / 消息队列
- 不要过度抽象（不要插件系统、不要多后端切换、不要配置文件系统）

【代码要求】
- 每个模块单一职责，单个文件不超过 300 行
- 关键函数的 docstring 说明"为什么这么做"，而不是复述"做了什么"
- 涉及 LSP 协议的地方注明对应的 method 名
- 错误处理要明确：LSP 超时、未就绪、进程崩溃时分别返回什么

【输出要求】
每实现一个模块后，按这个格式回复：
1. 这个模块解决什么问题
2. 完整可运行的代码
3. 验证方式（怎么跑、预期输出是什么）
4. 未解决的设计问题（诚实列出）
```

### 6.2 Phase 0 提示词

```
【Phase 0 目标】
环境准备 + 用真实 LSP 返回探明协议细节。不写任何正式代码，只做探测和存档。

【任务】

1. 环境准备
   - 安装 pyright（npm i -g pyright 或 pip install pyright）
   - 确认 `pyright-langserver --stdio` 能启动
   - 报告安装方式与实际可执行文件路径

2. 写一个一次性探测脚本 probe.py（不要放进正式模块结构）
   对一个小型 Python 仓库（20~50 个文件，或者就用 Aurora 的 backend/ 目录）：
   a. 启动 pyright-langserver，发送 initialize
      【重要】rootUri 必须设成仓库根的 file:// URI
              workspaceFolders 必须是 [{"uri": ..., "name": ...}]
      → 把 initialize 的完整返回 dump 成 probe/01_initialize.json
      → 特别确认 capabilities 里有没有 callHierarchyProvider
   b. 发送 initialized，然后 didOpen 一个文件
      → dump probe/02_didopen_diagnostics.json（等 publishDiagnostics 到达）
   c. 对该文件发 textDocument/documentSymbol
      → dump probe/03_document_symbol.json
   d. 挑一个函数符号，分别用 range.start 和 selectionRange.start 发
      textDocument/references（includeDeclaration: false）
      → dump probe/04a_refs_by_range.json 和 04b_refs_by_selection.json
   e. 对同一个符号发 callHierarchy/prepareCallHierarchy，
      再对返回的 item 发 callHierarchy/incomingCalls
      → dump probe/05_prepare_call_hierarchy.json 和 06_incoming_calls.json
   f. 对同一个符号发 textDocument/implementation
      → dump probe/07_implementation.json

3. 汇总答复（这是本阶段最重要的产出）
   基于真实 JSON 回答：
   - Q1: documentSymbol 里 range 和 selectionRange 的起点分别是哪一行哪一列？
         哪个才指向符号名？（这决定采点规则）
   - Q2: 用 range.start 查 references 是否真的返回空？用 selectionRange.start 呢？
   - Q3: incomingCalls 的返回结构长什么样？from 里有哪些字段？有没有 fromRanges？
   - Q4: textDocument/implementation 对 Python 类返回了什么？是有意义的继承关系还是空？
   - Q5: initialize 返回的 capabilities 里，callHierarchyProvider 的值是什么？

【明确不要做】
- 不要开始写正式模块（graph_store / builder / cli）
- 不要设计 schema
- 不要做并发优化
- 不要处理多个语言

【交付】
- probe/ 目录下 7 个真实 JSON
- 一份 probe/FINDINGS.md，逐条回答上面 5 个问题，引用具体 JSON 片段
先给我 probe.py 的代码。
```

### 6.3 Phase 1 提示词

```
【Phase 1 目标】
移植 Aurora 的 LSP 传输层，补上 callHierarchy，产出可查询的结构图。

【第一步：先读 Aurora 的代码】
在写任何代码之前，先读这四个文件并给我一份说明：
  D:\codex_Projects\Aurora\backend\lsp\client.py
  D:\codex_Projects\Aurora\backend\lsp\server_instance.py
  D:\codex_Projects\Aurora\backend\lsp\server_manager.py
  D:\codex_Projects\Aurora\backend\lsp\config.py
说明内容：
  - 每个文件的职责
  - 哪些代码可以原样复用，哪些必须改，为什么
  - 启动一个 LSP server 到能发请求，中间经过哪些步骤
我确认后再开始写代码。

【要实现的模块】

1. lsp/ 目录（移植 + 修改）
   移植 client.py / config.py（只留 pyright）/ server_instance.py / server_manager.py
   必须修：
     - server_instance.py 的 rootUri / workspaceFolders（改成仓库根目录）
     - server_instance.py 的 processId（改成 os.getpid()）
     - server_manager.py 的 includeDeclaration（改 false）
     - 加 atexit + signal 兜底，防止 pyright 变孤儿进程
   必须新增（server_manager.py 里）：
     - prepare_call_hierarchy(filepath, line, char)
     - incoming_calls(item)
     - outgoing_calls(item)

2. symbols.py（新写）
   - 调 textDocument/documentSymbol 拿文件内所有符号
   - 【关键】按 Phase 0 确认的规则采点（用 selectionRange.start，不是 range.start）
   - 生成限定名：module.Class.method，嵌套函数带父级
   - 生成 ID：{相对路径}::{限定名}
   - 第一版跳过 lambda / 匿名函数（无法稳定命名）

3. graph_store.py（新写）
   SQLite schema：
     symbols(id TEXT PRIMARY KEY, workspace, file, qualified_name, name, kind,
             start_line, start_col, end_line, end_col, signature, content_hash)
     edges(src_id, dst_id, kind, file, line)   kind ∈ calls | references | inherits
     索引：edges(src_id), edges(dst_id), symbols(workspace, file, content_hash)
   ⚠️ 这是符号索引的唯一真源，其他模块只能引用不能重复存储
   不存 implements（语义不匹配，见总纲）

4. builder.py（新写）
   - 遍历仓库 .py 文件（尊重 .gitignore）
   - 【性能要求】先批量 didOpen 所有文件，等诊断到达，再并发查询关系
     不要一个符号一个符号串行查（会超时）
   - 单文件失败跳过并记录，不中断整体
   - 输出进度

5. cli.py（新写）
   necessity index build <repo_path>
   necessity index callers <file>:<line>:<col>
   necessity index stats

【验收标准】
- 真实 Python 仓库（≥50 文件）跑通 build
- callers 能列出某函数的全部调用位置
- 人工标注 20 个符号（要混合：有调用方的 + 无调用方的 + 同名的）作为 ground truth
  计算 Precision 与 Recall，要求 P ≥ 0.90 且 R ≥ 0.85
- 100 文件仓库全量 build < 5 分钟

【明确不要做】
- 不要做增量更新（Phase 4）
- 不要做缓存层
- 不要做 Agent 集成
- 不要做 Web UI
- 不要支持 Python 以外的语言

按顺序做：先给「读代码说明」，确认后给 symbols.py，再 graph_store.py，依此类推。
```

### 6.4 Phase 2 提示词

```
【Phase 2 目标】
把结构图接进 Agent，让 Agent 在改代码前自动拿到影响面。

【要实现的模块】

1. impact.py
   impact_analysis(symbol_ref, depth=2) 返回：
     {
       callers: [{file, line, symbol, distance}],   # distance=1 直接调用方
       callees: [{file, line, symbol, distance}],
       tests:   [{file, test_name}],                # 按命名/目录约定识别
       risk:    "high" | "medium" | "low"
     }
   要求：结果带缓存；depth 默认 2

2. context_assembler.py
   assemble(target_symbol, token_budget) → 上下文字符串
   优先级（高→低）：直接调用方 > 目标符号本身 > 间接调用方 > 相关测试
   超预算时按优先级截断，并在末尾标注"因预算截断 N 项"

3. agent_tool.py
   暴露成 Agent 可调用的工具。
   description 必须写清：
     "在修改任何函数签名、移动函数、改变返回值之前调用此工具，
      获取全部调用方与受影响测试。不要用 grep 代替。"

【验收标准】
- 给 Agent 任务："把 foo() 改名为 bar()，并更新所有调用点"
- Agent 能在不 grep 的情况下拿到完整调用列表
- 8k token 预算内能包含全部直接调用方

【明确不要做】
- 不要做自动改代码（Agent 自己改，你只提供信息）
- 不要做 AST 重写
- 不要做跨语言
```

### 6.5 Phase 3 提示词

```
【Phase 3 目标】
量化"有结构图 vs 没有结构图"的差距。这是整个项目的核心产出。

【最重要的一条设计约束 —— 先读】
不要设计"改函数名"这类任务。Python 里 grep 找同名调用点的准确率很高，
如果任务集都是这类，graph 组不会有优势，整个实验会白做。

任务集必须刻意包含【grep 天生做不对】的场景，而且这类要占一半以上。

【任务集设计】

三类任务：
  A 类（基线，5 个）：单文件改函数名、简单加参数
      —— grep 也能做对，用来证明 graph 不比 grep 差（防倒退）
  B 类（区分性，12 个）：★ 主指标来源，每个都必须有同名干扰符号
  C 类（压力，5 个）：跨模块提取方法、接口重构

B 类必须覆盖这 5 种场景（每种 2~3 个）：
  1. 同名方法混淆：仓库里同时有 User.save() 和 Config.save()，只改其中一个
     —— grep 会把两个都找出来，必然误伤
  2. 动态调用：getattr(obj, "save")()
     —— grep 完全找不到
  3. 子类覆写 + 基类调用
     —— grep 只能找到一半
  4. 装饰器包装
     —— grep 找不到真实调用链
  5. 需要类型推断才知道的调用
     —— grep 找不到

【硬性要求】
每个 B 类任务的仓库里，必须存在一个与被改符号同名的干扰符号。
如果造不出这个条件，这个任务不算 B 类。这是实验能否成立的关键。

任务目录结构：
  tasks/001-same-name-method/
    repo/          起始代码快照
    task.md        重构要求（自然语言）
    tests/         验收测试（客观判定依据）
    meta.json      {task_id, category: "A|B|C", difficulty, files_touched,
                    expected_callers, decoy_symbols: [...]}

【跑分脚本】

runner.py 对每个任务：
  a. 复制快照到临时目录
  b. 用 Agent 执行 task.md
  c. 跑 tests/ 判定 pass/fail
  d. 记录：是否通过、token 消耗、工具调用次数、轮次
两种模式，同一任务各跑一次：
  baseline —— Agent 只有文件读写 + grep
  graph    —— Agent 额外有 impact_analysis 工具

report.py 汇总：
  - 主指标：破坏调用方的次数（定义：改动完成后仍存在调用旧签名/旧名字的地方，
            或验收测试因调用点未更新而失败）
  - 任务通过率，【必须按 A/B/C 分类统计】
  - 平均 token、平均轮次
  ⚠️ 只报总通过率会被 A 类稀释掉差距，必须分类报。B 类的差距才是卖点。

【验收标准】
- 一条命令跑完全部任务并输出表格
- B 类任务上 graph 组在主指标上明显优于 baseline
- A 类任务上 graph 组不比 baseline 差
- 数字可直接写进简历

【明确不要做】
- 不要用 LLM 当裁判（用测试用例判定，客观可复现）
- 不要为了指标好看而手工干预（哪怕结论是"没提升"也要如实记录）
- 不要在同一模式下混用两种配置

先给我 3 个 B 类任务的设计（含 repo 里同名干扰符号怎么造），我确认后再批量造。
```

---

## 7. 简历条目模板（Phase 3 完成后填数字）

**CodeGraph · 结构感知的编码 Agent（个人项目）**

针对编码 Agent"改代码不知道会炸谁"的问题，基于 LSP 构建代码结构图，为 Agent 提供改动影响面查询能力。

- **关系抽取**：移植并改造 LSP 客户端（JSON-RPC + Content-Length 分帧 + 进程生命周期管理），基于 `textDocument/documentSymbol` 与 `callHierarchy` 抽取符号级调用/引用/继承关系，构建符号-边结构图；不依赖文本匹配，可区分同名符号。
- **影响面分析**：实现 `impact_analysis` 工具，改代码前返回全部调用方、被调用方与相关测试，并按图距离与 token 预算组装上下文。
- **效果量化**：构建 __ 个跨文件重构任务的评测集（含 __ 个同名符号混淆场景），对比"仅 grep"与"结构图增强"两组，将**破坏调用方的次数从 __ 降至 __**，平均 token 消耗 __。

**技术栈**：Python / LSP / pyright / SQLite

---

## 8. 面试必答

1. **为什么用 LSP 而不是自己写解析器？**
   → 准确性是编译器级的；同名方法/重载只有类型信息才能区分；自写 parser 只能做名字匹配。

2. **已经有 tree-sitter 了，为什么还上 LSP？**
   → tree-sitter 只给语法作用域，**不做符号绑定**——`A.save()` 和 `B.save()` 在它眼里是同一个名字。而区分同名符号**正是本项目主指标的来源**。两者定位不同：tree-sitter 适合分块，LSP 适合解析后的语义关系。

3. **你的图和 IntelliJ 的 Call Hierarchy 有什么区别？**
   → IDEA 是给人交互式看的，不会替 Agent 决定"这次改动该往上下文里塞什么"。我做的是 **Agent 的检索层**，输出的是上下文而不是界面。

4. **为什么不做可视化？**
   → 我的用户是 Agent 不是人。可视化已经被 IDEA 和 Sourcegraph 做透了，做那个没有增量价值。

5. **图不准怎么办？**
   → 这是最大风险——错的图会让 Agent 自信地漏改。所以 Phase 1 就把抽查验到 P≥0.90/R≥0.85，且低置信度时**不返回**而不是返回错的。

6. **破坏率是怎么定义的？**
   → 改动完成后仍存在调用旧签名/旧名字的地方，或验收测试因调用点未更新而失败。

7. **为什么不用向量检索？**
   → 向量解决"找相似的代码"，解决不了"找结构上相关的代码"。两者互补。

8. **为什么不直接用 Aurora 的 LSP 代码？**（如果面试官知道 Aurora）
   → 传输层是复用的，但 Aurora 里 `callHierarchy` 和 `documentSymbol` **从未被使用**，而且它的 `initialize` 没传 `rootUri`，跨文件解析是隐式依赖 cwd 的。我补了这两块并修正了初始化参数。

---

## 9. 时间预算

| 阶段 | 时间 | 产出 |
|---|---|---|
| Phase 0 | 半天 | 7 个真实 JSON + 采点规则确认 |
| Phase 1 | 第 1 周 | 能建图、能查调用方（P/R 达标） |
| Phase 2 | 第 2 周 | Agent 能用上 |
| Phase 3 | 第 3 周 | **数字（核心产出）** |
| Phase 4 | 第 4 周（可选） | 增量更新 + 文档 |

**如果只有两周**：Phase 0 + Phase 1 + Phase 3 精简版（A3/B6/C2）。跳过 Phase 2 完整集成，改为手工把影响面喂给 Agent 做对比。

**如果只有一周**：Phase 0 + Phase 1 的采点与建图（不做并发优化），加上人工抽查 20 个符号的 P/R。产出是"我验证了 LSP 能准确抽取跨文件调用关系"——虽然弱，但比没有强。
