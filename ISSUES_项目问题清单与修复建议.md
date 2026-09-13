# Aurora 项目问题清单与修复建议

> **审查对象**：`D:\codex_Projects\Aurora`（v0.2.0）
> **代码规模**（2026-09-13 复测）：216 个 Python 文件 / 40,739 行；桌面端 `desktop/src` 38 个源文件
> （Python 统计含本轮并发编辑产生的临时探针文件，故为动态值；其中 git 已跟踪者为 212 个文件 / 39,645 行）
> **审查方法**：静态通读核心模块 + 全局调用链检索 + 运行时导入验证 + 服务启动冒烟 + 攻击用例构造 + 测试套件执行
> **文档性质**：可勾选的整改清单，每条含「现象 / 位置 / 影响 / 修复建议」

---

## 0. 审查范围与验证手段

| 验证项 | 手段 | 结果 |
|---|---|---|
| 服务可启动 | `python run_server.py` + HTTP 探测 | `/health` 200、`/docs` 200 |
| 路由完整性 | `import backend.api` 后统计 | 267 个路由对象 / 243 个 OpenAPI 路径（2026-09-13 复测修正） |
| 工具注册 | 运行时调 `register_all_tools()` 枚举 | 29 个工具 / 20 个分类 |
| 运行时依赖 | 检查 `sys.modules` | 921 个模块，**langgraph 0 个**（2026-09-13 复测修正） |
| 测试套件 | `pytest tests/` | 收集 446 个用例（初查）；2026-09-13 复测为 **516** |
| 路径越权修复 | 构造同级前缀目录攻击用例 | 修复生效（详见附录 A） |
| 密钥检测 | 植入 6 个密钥实际扫描 | 命中 7 条，分级正确 |

**关于测试失败的说明**：本文档初次审查环境为受限沙箱，`pytest` 曾出现 `26 failed / 84 errors`，**逐条追查后确认全部为沙箱写入权限导致（写 `~/.aurora` 与系统临时目录），非项目代码缺陷**。当前仓库已加入根 `conftest.py` 隔离 `AURORA_HOME`，**2026-09-13 复测 `python -m pytest tests/ -q` 结果为 `516 passed in 23.80s`，零失败**（本轮并发修复期间曾出现 11 failed，为其他 agent 编辑到一半的瞬时状态，最终收敛为全绿）。

---

## 1. 结论摘要

**核心引擎是真实可用的**：六阶段状态机、工具注册调度框架、路径越权修复、多供应商抽象、RAG 检索流水线均为真实实现，不是骨架。

**主要风险集中在两条线上**：

| 风险线 | 具体表现 |
|---|---|
| **安全默认值不安全** | 审批默认关闭、`approval_gate` 异常放行、Docker 沙箱恒失效且零调用、workspace 隔离为启发式 → **默认状态下命令直接跑在宿主机且无需确认** |
| **会话隔离缺失** | `AgentGraph` 为进程级单例，模型、Token 预算、取消集合跨会话共享 |

| 类别 | 数量 |
|---|---|
| 🔴 高危安全问题 | 12 |
| 🟠 架构 / 并发问题 | 2 |
| 🟡 功能性缺陷 | 14 |
| 🔵 工程质量问题 | 12 |
| **合计** | **40** |

---

## 2. 🔴 高危安全问题

### S1. 审批门是「异常放行」（fail-open）

- **位置**：`backend/tools/approval_gate.py:31-34`
- **现象**：

  ```python
  except ImportError:
      return None
  except Exception:
      return None      # ← 任何异常都放行
  ```

- **影响**：审批链路一旦抛异常（SSE 广播失败、`assess_risk` 逻辑错误、`wait_for_decision` 异常），工具**直接执行**，且无任何日志。安全组件应为 fail-closed。
- **调用方**（共 6 处）：`code_exec.py:103`、`computer_use.py:70`、`connector_tools.py:113`、`file_rw.py:46`、`mcp_proxy.py:119`、`web_fetch.py:65`
- **修复建议**：

  ```python
  except ImportError:
      return None                      # 模块缺失可容忍（此时审批系统整体不可用）
  except Exception as e:
      logger.error(f"approval gate failed, denying by default: {e}", exc_info=True)
      return f"denied (approval-error: {type(e).__name__})"
  ```

### S2. 审批默认关闭

- **位置**：`backend/approval.py:37`（`ApprovalManager(policy=NEVER)`）、`:285`（`approval_manager = ApprovalManager()`）、`:47-48`（NEVER 下 `needs_approval` 直接返回 False）；`backend/agent/graph.py:180,492`（`approval_mode="never"` 默认）；`backend/api/models.py:12`（`sandbox_mode="full-access"` 默认）
- **影响**：开箱状态下，Agent 可无确认执行 shell、写文件、删文件。
- **补充事实**：桌面端 WS 路径传的是 `approval_mode="on-request"`（`chat.py:140,149`），**产品形态下审批是开启的**；仅裸调 REST 接口默认关闭。
- **修复建议**：`ApprovalManager` 默认改为 `ON_REQUEST`；`graph.run()` 的 `approval_mode` 默认值改为 `"on-request"`；`models.py` 的 `sandbox_mode` 默认改为 `"workspace-write"`。

### S3. Docker 沙箱恒失效，且全项目零调用

- **位置**：`backend/sandbox/__init__.py:48`

  ```python
  dangerous_chars = {'&', '|', ';', '$', '', '(', ')', '{', '}', '<', '>', '\n', '\r'}
  #                                             ^^^^ 空字符串
  if any(c in cmd for c in dangerous_chars):
      return {"success": False, ..., "stderr": "Command contains forbidden shell characters"}
  ```

- **影响**：`'' in cmd` **恒为 True** → `execute()` 对任何命令都立即返回失败，该沙箱**从未成功执行过任何命令**。且 `DockerSandbox` 全项目仅被 `backend/__init__.py:24` 重新导出，**无任何调用点** → `Dockerfile.sandbox` 完全未被使用，**shell 命令始终在宿主机原生执行**。
- **修复建议**：二选一，不可含糊：
  - **接入**：删除 `''`（原意疑为反引号），并把 `shell_command` 的执行路径改为经 `DockerSandbox.execute()`；
  - **移除**：删除 `backend/sandbox/` 与 `Dockerfile.sandbox`，避免「有沙箱」的错觉。

### S4. 命令白名单可被链式绕过

- **位置**：`backend/tools/shell_command.py:45-52`（`_is_whitelisted` 只取 `command.strip().split()[0]`）；`:132`（`asyncio.create_subprocess_shell`，经 shell 解释）
- **现象**：`git status; <任意命令>`、`ls && <任意命令>` 的 `base_cmd` 分别为 `git`/`ls`，**通过白名单**。`python` 亦在白名单内（`:30`），故 `python -c "..."` 可执行任意代码。
- **现状兜底**：`bash_classifier.classify_pipeline()` + `InputSanitizer.sanitize_shell_command()` 两道启发式（`shell_command.py:114-127`），以及 workspace 边界的轻量检查。属纵深防御，非可靠屏障。
- **修复建议**：拒绝含 `;`、`&`、`|`、`` ` ``、`$(` 的命令；或改用 `create_subprocess_exec` 不经 shell；`python -c` / `node -e` 这类「解释器直执行」应单列审批或禁用。

### S5. shell 超时不杀死子进程

- **位置**：`backend/tools/shell_command.py:139-149`

  ```python
  stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
  ...
  except asyncio.TimeoutError:
      return {"success": False, ...}      # ← 从未调用 proc.kill()
  ```

- **影响**：超时后子进程继续在后台运行，反复超时会导致进程堆积。
- **修复建议**：在 `except asyncio.TimeoutError` 分支中 `proc.kill()` 并 `await proc.wait()`。

### S6. 环境变量全量透传（API Key 泄漏）

- **位置**：`backend/tools/shell_command.py:137`

  ```python
  env={**os.environ, "PYTHONUNBUFFERED": "1", "NODE_OPTIONS": "--max-old-space-size=512"}
  ```

- **影响**：`AURORA_LLM_API_KEY`、`AURORA_VISION_API_KEY` 等全部注入每个被执行的子进程，任何被执行的代码（含被处理的第三方项目代码）都能读取。
- **修复建议**：改为白名单式传参，仅传入构建工具必需的变量（`PATH`、`HOME`、`LANG` 等），敏感变量显式剔除。

### S7. workspace-only 沙箱为启发式，可平凡逃逸

- **位置**：`backend/agent/graph.py:834-841`（仅注入 `_workspace_boundary=True` 标志）；`backend/tools/shell_command.py:55-77`（`_violates_workspace` 只查 `cd /`、`>> C:\`、`/etc/` 等字面量）
- **可逃逸示例**（均不触发任何规则）：

  ```
  python -c "import os; os.chdir('C:/Windows'); ..."     # 无 cd 关键字、无重定向
  cp /etc/passwd ./                                       # 读敏感文件，非写操作
  tar -C / -cf x.tar Windows                              # 无 cd / 无重定向
  ```

- **影响**：结合 S4（`python` 在白名单内），workspace 边界**不具备实际约束力**。
- **修复建议**：shell 子系统应在受限账户或容器内执行；路径类参数应逐个经 `safe_resolve_path` 校验；至少应禁止 `python -c` / `node -e` / `tar -C` 等到绝对路径的形式。

### S8. 配置项 `sandbox_mode` 从未被读取

- **位置**：`backend/config/__init__.py:195-196`
- **影响**：`config.sandbox_mode`（默认 `"workspace-write"`）全项目**无任何读取点**，用户配置的沙箱模式被完全忽略；实际生效的是请求参数默认值 `"full-access"`。
- **修复建议**：`api/models.py` 的默认值改为从配置读取，或在 `get_graph()`/`run()` 中回落到 `cfg.sandbox_mode`。

### S9. `shell.openExternal` 无协议白名单

- **位置**：`desktop/src/main/index.ts:706-707`
- **影响**：渲染层可传入任意 URL/协议（`file://`、自定义协议处理器），在用户机器上拉起本地程序或打开本地文件。
- **修复建议**：只允许 `http:` / `https:` 前缀，其余一律拒绝并记录。

### S10. 渲染层无 CSP，主窗口未启用 sandbox

- **位置**：`desktop/index.html`（无 `Content-Security-Policy` meta，且远程加载 `fonts.googleapis.com`）；`desktop/src/main/index.ts:107-110`（主窗口设了 `nodeIntegration:false` + `contextIsolation:true`，但**未设 `sandbox:true`**；仅第二个窗口 `:173-175` 设了）
- **影响**：无 CSP 削弱 XSS 纵深防御；远程字体在离线环境降级并扩大攻击面。
- **修复建议**：补 CSP meta；字体本地化；主窗口补 `sandbox: true`。

### S11. 后端安全响应头是死代码

- **位置**：`backend/security/__init__.py:291-319`（`SecurityHeaders.get_headers()` / `apply_to_response()`）
- **影响**：定义了 CSP、`X-Frame-Options`、HSTS 等，**但从未被任何 FastAPI 响应应用**（仅被 `tests/test_security.py:148` 引用）。且其 CSP 含 `script-src 'unsafe-inline'`。
- **修复建议**：加一个 `@app.middleware("http")` 统一注入响应头，或明确删除该类。

### S12. 认证白名单过宽

- **位置**：`backend/api/__init__.py:110`（`_AUTH_FREE_PREFIXES`）
- **实测结果**（无任何凭据访问）：

  | 端点 | 结果 |
  |---|---|
  | `/files` `/tools` `/threads` `/approval/pending` `/observability/stats` `/tasks` `/re/sessions` `/remote/status` `/marketplace` | **401** ✅ 认证生效 |
  | `/sessions` `/memory/stats` `/agents/tree` `/plugins` `/config` `/providers` | **200** ⚠️ 免认证放行 |

- **缓解事实**：服务默认绑定 `127.0.0.1`（回环）；`/config` 返回的 `api_key` 已脱敏为全是 `*`。
- **修复建议**：从白名单中移除 `/config`、`/providers`、`/sessions`、`/memory`、`/agents`、`/browser`、`/connectors`、`/plugins`。

---

## 3. 🟠 架构 / 并发问题

### A1. `AgentGraph` 是进程级单例，被所有会话共享

- **位置**：`backend/api/deps.py:68-85`（`get_graph()` 返回缓存的单例 `_graph`）
- **共享的可变状态**：

  | 字段 | 位置 | 后果 |
  |---|---|---|
  | `self.llm.set_model(model)` | `graph.py:204,518` | 按请求改写**共享** LLM 客户端 → 并发会话模型互相覆盖 |
  | `self.token_budget` | `graph.py:77` | 全局**单一**预算，却在 `:380` 报 "Session token budget exhausted." → 一个会话耗尽所有人配额，且措辞误导 |
  | `self._last_tracked_tokens` | `graph.py:83` | 跨会话共享 → Token 记账被并发破坏 |
  | `self._cancelled_sessions` | `graph.py:82` | 取消信号跨会话共享 |

- **影响**：并发请求下行为不可预期；`AgentGraph` 事实上不是会话隔离边界，但被当作每会话引擎使用。
- **修复建议**：按 `session_id` 维护实例字典（带淘汰），或把上述状态改为随调用显式传入。

### A2. 工具串行执行，与注释不符

- **位置**：`backend/agent/nodes.py:199-211`（`for inv in state.tool_invocations:` 内逐个 `await tool_handler(...)`）
- **实测**：`executor_node` 中 `asyncio.gather` 不存在、`create_task` 不存在
- **影响**：一轮内多个独立工具调用（如读 5 个文件）串行执行，I/O 延迟线性叠加。
- **不符之处**：`backend/agent/graph.py:1` 的注释声称「LangGraph StateGraph — 真实状态图 + SSE事件集成 + 条件边 + **Send 并行分发**」，但 `Send(` 在 `graph.py` 中不存在。
- **修复建议**：改为 `asyncio.gather(*[handler(...) for inv in ...])`（需注意工具间可能存在顺序依赖，可加并发上限与只读工具白名单）。

---

## 4. 🟡 功能性缺陷

### B1. 并行工具调用会构造畸形的对话历史

- **位置**：`backend/agent/nodes.py:156-174`

  ```python
  if tool_calls:
      for tc in tool_calls:                      # ← 循环开始
          ...
          state.tool_invocations.append(inv)
          state.add_message(Message.assistant(   # ← 在循环内追加
              content=content or f"Calling {inv.name}",
              tool_calls=tool_calls,             # ← 每次都带全部 tool_calls
          ))
  ```

- **影响**：N 个并行工具调用 → 追加 **N 条完全相同的 assistant 消息**（每条都携带全部 N 个 `tool_calls`），后续 `tool` 结果消息配对错乱。下一次 LLM 调用会收到非法历史（重复 `tool_call_id`、未配对消息），模型行为不可预期。
- **正确形态**：1 条 assistant 消息携带全部 `tool_calls`，其后紧跟 N 条 `tool` 结果消息。
- **修复建议**：把 `add_message` 移出循环，循环只负责收集 `ToolInvocation`。

### B2. `max_turns` 双重自增，实际只跑一半

- **位置**：`backend/agent/nodes.py:117`（`state.total_turns += 1`）与 `backend/agent/graph.py:438`（`state.total_turns += 1`）在同一轮迭代内各执行一次；`graph.py:352` 用 `state.total_turns >= self.max_turns` 判断退出
- **影响**：配置 `agent.max_turn_iter: 30` 实际仅约 15 轮真实迭代。
- **修复建议**：移除其中一处（建议保留 `tool_select_node` 内的计数，删除 `graph.py:438`）。

### B3. 任务监视器永不启动（初始化顺序错误）

- **位置**：`backend/agent/graph.py:115-120` 检查 `self._monitor`，而 `self._monitor = get_monitor()` 在 `:133-137` 才赋值
- **影响**：`:115` 处 `self._monitor` 恒为 `None`（在 `:78` 初始化为 `None`），该分支永不成立 → 监视器的 `start(60)` 从未被排入 `_pending_tasks`。功能缺失 + 死代码。
- **修复建议**：把 `_monitor` 的初始化提到检查之前。

### B4. 检查点 undo / redo / list 接口完全失效

- **位置**：`backend/api/routes/sessions.py:58`、`:68`、`:78` —— 每个请求内 `mgr = CheckpointManager()` **新建实例**，而 `_undo_stack` / `_redo_stack` / `_workspace_states` 是实例内存态（`checkpoint.py:53-57`）
- **叠加问题**：`save_workspace_state()`（`checkpoint.py:222`，唯一向 `_undo_stack` 写入的方法）**全项目零调用点** → undo 栈永远为空
- **影响**：`/checkpoint/undo`、`/checkpoint/redo` 恒返回 `"Nothing to undo"` / `"Nothing to redo"`；`/checkpoint/list` 恒为空。且这三处与 `graph.py:73` 的图实例不是同一对象。
- **修复建议**：改为模块级单例（或注册表按 session 取实例）；在文件编辑工具中真正调用 `save_workspace_state()`。

### B5. `resume()` 无 API 出口

- **位置**：`backend/agent/graph.py:858`（`async def resume(self, checkpoint_id)`）
- **影响**：检查点快照确实落盘（`checkpoint.py:85-87`）且 `load()` 能重建 `AgentState`（`:103-125`），但**没有任何路由暴露 `resume()`**，用户无法使用断点续跑。
- **修复建议**：新增 `POST /checkpoint/resume {checkpoint_id}` 路由，调用 `graph().resume(cid)` 并以 SSE 或 WS 流式输出。

### B6. 取消会话会删除检查点

- **位置**：`backend/agent/graph.py:930`（`cancel()` 中 `self.checkpoints.clear_session(session_id)`）
- **影响**：取消操作销毁该会话的全部快照文件，恰好删掉了用户想恢复的东西。
- **修复建议**：取消不应清理检查点；如需清理，应由独立的显式接口承担。

### B7. RAG 只注入 REST 路径，桌面端不注入

- **位置**：
  - 有注入：`chat.py:47-52`（`POST /chat`）、`chat.py:67-72`（`POST /chat/stream`）
  - **无注入**：`chat.py:144-154`（`WS /ws/desktop`）、`chat.py:304-312`（`WS /ws/{session_id}`）—— 仅传 `user_text`

- **影响**：桌面端（`main/index.ts:579` 经 `/ws/desktop` 发送 `{type:"chat"}`）**完全不注入 RAG 与 skills 上下文** → 产品形态下 RAG 从不生效。
- **修复建议**：把 `chat.py:47-52` 的注入逻辑抽成 `_build_full_prompt(user_text)`，在两个 WS 分支中复用。

### B8. RAG 的向量检索在默认配置下静默失效

- **位置**：
  - `backend/agent/llm_providers.py:464-466`：`OpenAIProvider.embeddings()` 请求 `{base_url}/embeddings`
  - 当前配置：`provider=deepseek`、`base_url=https://api.deepseek.com`（`aurora.json`）
  - `backend/rag/engine.py:202`：`except Exception: logger.debug('rag search failed', exc_info=True)`

- **影响**：DeepSeek 不提供 embeddings 接口 → 请求失败 → **异常被 `logger.debug` 静默吞掉** → `vec_results` 为空 → 「关键词 + 向量混合检索」**退化为纯 BM25 关键词检索**，且无任何可见提示。索引侧同样：`VectorStore.embed_all()` 中 `except Exception: break` 会让 `_embeddings` 全空。
- **修复建议**：① 把 `logger.debug` 提升为 `logger.warning` 让降级可见；② 为 embedding 单独配置供应源（OpenAI 或本地 `sentence-transformers`），不复用 chat provider。

### B9. 向量索引无持久化，重启即空

- **位置**：`backend/rag/engine.py:57-119`（`VectorStore`）
- **现象**：构造函数接收 `persist_dir="./chroma_db"` 并保存为 `self.persist_dir`，**此后从未被使用**；向量检索是纯 Python 双循环点积（`:103-114`），无 numpy、无落盘；`_indexed` 为内存 `set`。
- **影响**：进程重启后 `vector_store.count() == 0`，而 `chat.py:47` 的前置条件是 `count() > 0` → **RAG 静默关闭**，需用户重新手动建索引。「代码知识库」名不副实（无持久化知识库）。
- **修复建议**：用 numpy 替换双循环；把 chunks + embeddings 落盘（npz + JSON）并在启动时加载。

### B10. 导入时触发网络下载，离线环境无法启动

- **位置**：`backend/context/token_counter.py:55`（模块级 `counter = TokenCounter()`，默认 `model="gpt-4o"`）
- **机制**：`gpt-4o` → `o200k_base` 编码，tiktoken 在首次使用时**联网下载 BPE 文件**
- **实测证据**（冷缓存环境）：

  ```
  requests.exceptions.SSLError: ... /encodings/o200k_base.tiktoken
  ERROR tests/test_agent.py - requests.exceptions.SSLError
  ... Interrupted: 9 errors during collection
  ```

- **影响**：无网络或处于 TLS 拦截代理后的机器上，**后端连导入都会失败**；CI 必须联网。
- **现状（2026-09-13 复测）**：**已修复**。`token_counter.py` 在未提交的工作区改动中已改为惰性解析编码器 + `_ENCODER_CACHE` + `_OFFLINE` 哨兵降级，`__init__` 不再联网。因此当前测试套件**可离线通过**（本次 `python -m pytest tests/ -q` 在无额外网络配置下全绿），新增的 CI 无需联网拉取 BPE 文件即可运行测试。
- **修复建议**（原始）：改为惰性初始化 + 离线兜底：

  ```python
  def _get_encoder(model):
      try:
          return tiktoken.encoding_for_model(model)
      except KeyError:
          pass
      try:
          return tiktoken.get_encoding(MODEL_ENCODINGS.get(model, "cl100k_base"))
      except Exception:
          return None          # 退化为 len(text)/4 近似计数
  ```

### B11. 历史折叠时 system 消息被静默丢弃

- **位置**：`backend/context/collapse.py:56-69`（`_summarize` 只有 `user` / `assistant` / `tool` 三个分支，**无 `system` 分支**）
- **叠加问题**：`backend/thread_follower.py:86` 恰恰把用户插话指示存为 system 消息：`record.messages = [*record.messages, {"role": "system", "content": instruction}]`
- **影响**：用户中途的约束指令（如「不要改数据库 schema」）在压缩时静默消失。
- **修复建议**：补分支 `elif role == "system": lines.append("System: " + content)`。

### B12. LLM 摘要压缩器完全未接线（死代码）

- **位置**：`backend/context/context_manager.py:32`（`should_compact`）、`:137`（`needs_compaction`）、`:161`（`compact_async`）、`:65-107`（`LLMCompactor`，会调模型生成 ≤500 词交接摘要）
- **实测**：以上符号除 `context_manager.py` 自身外**无任何引用**（仅 `backend/__init__.py:14` 重新导出）
- **影响**：主循环达到预算上限时的行为是**中止**（`graph.py:376-382`），而非压缩续跑。真正运行的折叠是 `ContextCollapser` 的**规则式字符串截断**，不调用模型。文档中「自动触发上下文压缩 / 总结为摘要」与实际不符。
- **修复建议**：在 `graph.py` 主循环中加入：

  ```python
  if self.token_budget.usage_ratio() >= 0.85:
      await self.context_manager.compact_async(self.llm)
  ```

### B13. `truncate_tool_output` 是死代码

- **位置**：`backend/agent/nodes.py:361`（定义）、`backend/agent/graph.py:15`（import）、`backend/agent/__init__.py:8,18`（导出）
- **影响**：仅被 import 与导出，**从无调用**。实际生效的是 `nodes.py:222` 的 `[:8000]` 硬切。
- **修复建议**：删除，或改用其作为统一截断入口。

### B14. `langgraph_adapter.py` 是死代码，但文档声称使用了 LangGraph

- **位置**：`backend/agent/langgraph_adapter.py`（87 行，含完整 `StateGraph` / `add_conditional_edges` / `MemorySaver` 实现）
- **实测（2026-09-13 复测）**：
  - `langgraph` 字样共 27 处（`grep -rn "langgraph\|LangGraph" --include=*.py --include=*.md --include=*.txt`，排除 `node_modules`）：`langgraph_adapter.py` 自身 9 处、本文档 13 处（含本次更正说明）、`graph.py` 注释 2 处、`README.md` 2 处、`requirements.txt` 1 处。**除 `langgraph_adapter.py` 自身外无任何调用点**
  - 导入整个后端后 `sys.modules` 中 **langgraph 0 个**（共 921 个模块）
  - `importlib.util.find_spec("langgraph")` 返回 `None` → **langgraph 未安装**
  - `graph.py` 中 `StateGraph(` / `add_node` / `add_edge` / `add_conditional_edges` / `.compile(` / `Send(` **全部不存在**
- **影响**：生产引擎是 `graph.py` 的自研状态机（`while not state.done`），但以下表述与现实不符（**已于 2026-09-13 全部修正**）：
  - `README.md`（简介 / 项目结构 / 核心能力 / 技术栈）——原「基于 LangGraph StateGraph 构建」，已改为「自研手写状态机」
  - `backend/agent/graph.py:1`——原「LangGraph StateGraph — 真实状态图 + … + Send 并行分发」，已改为自研状态机描述
  - `backend/agent/graph.py:34`——原「基于 LangGraph StateGraph 的六步流水线」，已改为「自研六阶段状态机」
- **修复建议**：统一改为「自研六阶段状态机」；`requirements.txt` 的 `langgraph` 若保留需注明「仅供 `langgraph_adapter.py` 可选实现使用，主链路不依赖」；`langgraph_adapter.py` 的 docstring 明确标注为原型期产物且未被引用。
- **已完成的处置**：README 与 `graph.py` 两处注释的表述已修正；`requirements.txt` 已加尾部注释说明该依赖仅供未使用的可选适配器（**依赖行本身按约定保留**）；`langgraph_adapter.py` 文件按约定**未删除、未修改**。

---

## 5. 🔵 工程质量问题

### Q1. 空行膨胀——原表数据有误，已重新实测

> **更正说明（2026-09-13 复测）**：下表旧数据不可用，已全部作废并替换。
> 旧表的行数/空行数两个维度都错得自相矛盾：例如 `state.py` 声称「总行数 394 / 空行 216」，
> 但该文件实际只有 201 行，**216 个空行在物理上不可能**；`apply_patch.py` 与 `plugin_marketplace.py`
> 的旧行数（885、451）也远大于实际（456、226）。
> 旧数据的量级与形态（行数约为实际的 1.9～2.0 倍、空行占比集中在 50%～58%）
> 更像是来自**另一个数据集或早期被整体复制粘贴拉长的中间版本**，而非本仓库当前源码。
> 本次全部改用 Python 以二进制读取 + UTF-8 解码后再按 `\n` 切分统计
> （`open(f,'rb').read().decode('utf-8')`，末尾空元素剔除），以规避 CRLF 换行导致 `grep -c '^$'` 少计空行的问题。
> 空行定义：`line.strip() == ''`。

| 文件 | 总行数 | 空行 | 占比 | 换行符 |
|---|---|---|---|---|
| `backend/tools/apply_patch.py` | 456 | 69 | 15.1% | CRLF |
| `backend/agent/state.py` | 201 | 23 | 11.4% | CRLF |
| `backend/agent/checkpoint.py` | 370 | 202 | **54.6%** | CRLF |
| `backend/plugin_marketplace.py` | 226 | 18 | 8.0% | CRLF |
| `backend/prompt_templates.py` | 443 | 234 | **52.8%** | CRLF |
| `desktop/index.html` | 32 | 14 | 43.8% | CRLF |
| `backend/agent/graph.py` | 995 | 351 | 35.3% | CRLF |

- **复测结论（与旧表结论不同，据实修正）**：空行膨胀问题**远小于旧表所述**，且并非普遍现象。
  7 个文件中只有 3 个真正偏高：`checkpoint.py`（54.6%）、`prompt_templates.py`（52.8%）、
  `graph.py`（35.3%）；其余 4 个处于 8%～16% 的正常区间（`desktop/index.html` 因是短小 HTML，
  43.8% 的占比对应绝对值仅 14 行，不具实际影响）。
  因此**不支持旧表「7 个文件普遍被人为拉长到 50%+ 空行」的框架**——那是由错误的原始数据得出的。
- **影响（按修正后数据评估）**：仅 `checkpoint.py` / `prompt_templates.py` / `graph.py` 三个文件
  的空行值得整理，属可读性与 diff 噪声层面的中低优先级问题，**不构成「源码被人为拉长」的质量事故**。
- **修复建议**：对上述 3 个文件执行 `ruff format` / `black` 还原正常空行；其余文件无需专门处理。

### Q2. 测试无隔离，直接读写真实用户目录

- **位置**：`backend/auth.py:11` —— `DEFAULT_DATA_DIR = Path(os.environ.get("AURORA_HOME", Path.home() / ".aurora"))`，模块级求值
- **叠加问题**：本文档初次审查时全仓库**没有 `conftest.py`，也没有 `pytest.ini` / `pyproject.toml` / `setup.cfg`**（当时 25 个测试文件零共享夹具）
- **现状（2026-09-13 复测）**：根 `conftest.py` 已存在并隔离 `AURORA_HOME`；`pyproject.toml` **仍缺失**（本次已按另一交付项补上）。
- **影响**：测试会创建/删除开发者真实的 `~/.aurora/tokens.enc`；测试不可重入，且无法在受限沙箱或干净 CI 中运行（初次审查即因此出现 84 errors）。
- **修复建议**：保留根 `conftest.py`（以 `monkeypatch.setenv("AURORA_HOME", tmp_path)` 隔离）；引入 `pyproject.toml` 统一 pytest 配置。

### Q3. 无 CI

- **现象**：`.github/` 不存在，无任何流水线配置
- **现状（2026-09-13）**：本次交付已新增 `.github/workflows/ci.yml`（Windows + Linux 矩阵，安装依赖 + `pytest` + `ruff check`）。
- **修复建议**：加最小工作流（安装依赖 + `pytest` + `ruff check`）。

### Q4. 依赖声明问题

- **位置**：`requirements.txt`
- **问题**：
  - 全部使用 `>=` 松版本，无锁文件
  - **运行时依赖与开发依赖混在一起**（`pytest`、`pytest-asyncio` 在运行列表内）
  - `langgraph` 已声明但**未安装**（见 B14）
  - `chromadb` / `sentence-transformers` 声明了，但 RAG 实现并未真正使用 ChromaDB（`persist_dir` 未启用），`sentence-transformers` 仅用于可选重排
- **修复建议**：迁移到 `pyproject.toml`（区分 `dependencies` 与 `optional-dependencies`），移除未实际使用的重依赖或标注为可选。

### Q5. README 数字与实际不符

| README 声称 | 实测（2026-09-13 复测） |
|---|---|
| `436 passed`（徽章）/ `391 tests`（结构节同项两处不一致） | 收集 **516** 个用例（`516 passed in 23.80s`；本轮并发修复新增用例后从 446 增至 516） |
| `100+ REST + WebSocket 端点` / `100 个 REST + WebSocket 端点` | **243** 个 OpenAPI 路径 / 267 个路由对象（114 GET、130 POST、3 PUT、14 DELETE、2 WebSocket） |
| 桌面端与后端同版本 `0.2.0` | `desktop/package.json` 原为 `0.1.0`，**已对齐为 `0.2.0`** |
| `15+ 工具` → 正文写「17+」 | **29** 个工具 / 20 个分类 |

- **修复建议**：把测试数与端点数改为脚本自动统计写入，避免继续漂移。

### Q6. 重复实现

| 重复项 | 位置 |
|---|---|
| 两个 `TokenCounter` | `backend/agent/llm_client.py:61` 与 `backend/context/token_counter.py:20` |
| 两个 `TokenBudget` | `backend/context/token_budget.py:6` 与 `backend/context/token_tracker.py:6`（`graph.py` 用后者） |
| 两个相同函数 | `backend/api/deps.py:124`（`ensure_all`）与 `:134`（`ensure_deps`）功能完全一致 |
| URL 探测重复 | `graph.py:157-175` 已抽出 `_detect_url()`，但 `run_with_stream` 在 `:570-596` 内联重写 |
| 两个 `truncate` 实现 | `tools/base.py:72` 与 `agent/nodes.py:361` |

- **修复建议**：各自合并为单一实现，删除冗余副本。

### Q7. 桌面端构建产物过期

- **实测（2026-09-13 复测）**：`desktop/dist/main/index.js` 时间戳 `8月27日 22:43`，而 `desktop/src/renderer/components/chat/ChatPanel.tsx` 修改于 `22:45` —— **dist 确比源码旧**。
- **重要澄清**：`desktop/dist/` 已被 `.gitignore` 排除（`git ls-files desktop/dist` 为空），**该产物从未进入版本控制**，因此不存在「提交了过期构建产物」的问题，只是本地磁盘上的产物陈旧。
- **已处置**：已执行 `cd desktop && npm run build`（`tsc -p tsconfig.main.json && vite build`），**构建成功**（85 modules transformed，renderer 产物 324.79 kB / gzip 94.44 kB），`dist/main/index.js` 已刷新为最新，不再旧于源码。由于 dist 未跟踪，此项无版本控制层面的交付物。
- **修复建议**：保持构建产物不入库；如需可复现构建，应在 CI 中构建而非提交 dist。

### Q8. 大量未提交的工作处于风险中

- **实测（初查）**：`git status --porcelain` 显示 **45 个文件被修改**，另有 **2 个新文件未跟踪**（`backend/agent/roles_loader.py`、`backend/tools/approval_gate.py`）；而 `master` 与 `origin/master` 完全同步（0 ahead / 0 behind）
- **复测（2026-09-13）**：`git status --porcelain` 为 **20 个已修改 + 6 个未跟踪**；`master...origin/master` 仍为 `0 ahead / 0 behind`。两批 P0 安全修复已提交（`74b1dd1`、`23012b8`），未提交量已从 45 降至 20。**仍有未提交改动处于风险中**
- **影响**：`GAP_ANALYSIS_企业级差距分析.md` 中声称已完成的多项修复**只存在于工作区，未进入版本控制**，一次误操作即全部丢失。
- **修复建议**：立即分批提交。

### Q9. i18n 是表面数字

- **实测**：`backend/i18n/` 共 **65 个文件、每个仅 21 个 key**（纯导航/按钮级）；渲染端 `i18n.ts`（446 行）有 417 个 key 但仅中英；组件内仍有大量硬编码中文。
- **修复建议**：统一前后端 key 体系，按需增量翻译，不要用语言文件数量充数。

### Q10. Swarm 子进程后端是「假跑」

- **位置**：`backend/swarm/backends.py:165`（引导脚本内容为 `"    while True: time.sleep(3600)"`）
- **叠加问题**：`TMUX` / `REMOTE` 后端在 `:15-16` 枚举中声明但无实现
- **影响**：`TerminalBackend.spawn()` 生成的进程只打印任务描述后空转，不执行任何 agent 逻辑；`send_message` 在 win32 下走 `cmd /c start` 无 stdin 管道 → 静默 no-op。
- **修复建议**：改为真实运行 `python -m backend.cli --task ...`，或删除伪实现并明确标注为 UI 占位。

### Q11. 简单对话判定依赖巨型硬编码关键词表

- **位置**：`backend/agent/graph.py:229-241` 与 `:530-536`（`is_simple_chat`，含约 40 个中英关键词，且两处各写一份）
- **影响**：判定脆弱，容易把真实任务误判为寒暄而绕过整个流水线（例如含「改」字即走流水线，但「帮我看看这个」会走短路）。
- **修复建议**：改为单次轻量 LLM 分类，或至少把关键词表提取为配置项并去重。

### Q12. 扫描器单例缓存首个 workspace

- **位置**：`backend/security_scanner.py:316-320`

  ```python
  def get_scanner(workspace: str = ".") -> SecurityScanner:
      global _scanner
      if _scanner is None:
          _scanner = SecurityScanner(workspace)   # 首个调用者的 workspace 被永久固定
      return _scanner
  ```

- **影响**：后续传入不同 workspace 会被忽略，导致对错误的目录做扫描（或路径相对化失败）。
- **修复建议**：按 workspace 做键的字典缓存。

---

## 6. 修复路线图

### P0 — 立即（安全止血，改动小、收益大）

| # | 事项 | 工作量 |
|---|---|---|
| 1 | S1 审批门改为 fail-closed | 3 行 |
| 2 | S2 审批默认策略改为 `on-request`，`sandbox_mode` 默认改 `workspace-write` | 3 行 |
| 3 | S3 删除 `sandbox/__init__.py:48` 的空字符串，并决定接入或删除 Docker 沙箱 | 1 行 + 决策 |
| 4 | S5 超时分支补 `proc.kill()` | 3 行 |
| 5 | S6 环境变量改为白名单传参 | ~10 行 |
| 6 | S12 收紧 `_AUTH_FREE_PREFIXES` | 1 行 |
| 7 | **Q8 提交未提交文件**（复测时 20 个已修改 + 6 个未跟踪） | 一次 commit |

### P1 — 正确性与并发（1–2 周）

| # | 事项 | 工作量 |
|---|---|---|
| 8 | A1 按会话创建 `AgentGraph`，消除状态串扰 | ~30 行 |
| 9 | B1 修正并行工具调用的 assistant 消息构造 | ~10 行 |
| 10 | B2 去掉重复的 `total_turns` 自增 | 1 行 |
| 11 | B4 + B5 修复检查点接口（单例化 + 暴露 `resume`） | ~25 行 |
| 12 | B3 修正 monitor 初始化顺序 | 移动 5 行 |
| 13 | B10 `TokenCounter` 改惰性初始化 + 离线兜底 | ~15 行 |
| 14 | B11 折叠器补 `system` 分支 | 2 行 |
| 15 | B8 让 RAG 降级可见 + 独立 embedding 配置 | ~15 行 |
| 16 | B7 桌面端 WS 路径注入 RAG / skills | ~10 行 |
| 17 | A2 工具改并发执行（`asyncio.gather` + 并发上限） | ~20 行 |
| 18 | S4 shell 拒绝链式元字符 | ~10 行 |

### P2 — 工程质量（1 个月）

| # | 事项 |
|---|---|
| 19 | ~~Q2 新增 `conftest.py` + `pyproject.toml`~~ **已完成**（`conftest.py` 已在；`pyproject.toml` 本次新增） |
| 20 | ~~Q3 加最小 CI 工作流~~ **已完成**（`.github/workflows/ci.yml`，Windows + Linux） |
| 21 | Q1 清理 3 个文件的空行膨胀（复测后仅 `checkpoint.py` / `prompt_templates.py` / `graph.py` 需要） |
| 22 | Q6 消除 5 处重复实现 |
| 23 | B12 把 `compact_async` 接进主循环（使「自动压缩」成立） |
| 24 | B9 `VectorStore` 改 numpy + 落盘持久化 |
| 25 | ~~Q5 校正 README 数字；B14/Q5 统一 LangGraph 表述为「自研」~~ **已完成**（README 数字与 B14 表述、`graph.py` 两处注释均已修正） |
| 26 | ~~Q7 重新构建桌面端~~ **已完成**（构建成功，dist 本地刷新）；Q9/Q10 处理 i18n 与 swarm 伪实现 |

---

## 7. 客观记录：已经做对的部分

审查中确认以下为真实、正确的实现，应在整改时保留：

| 项 | 证据 |
|---|---|
| **路径越权修复正确** | `tools/base.py:46-60` 使用 `resolved == ws or resolved.is_relative_to(ws)`，`.resolve()` 在判断前执行；攻击用例实测被拦截（附录 A） |
| **第二道路径防线** | `api/path_security.py:56-86` 独立实现，按 `path.parents` 判断包含关系，支持配置多个允许根 |
| **工具框架抽象清晰** | `tools/base.py` 的 `ToolSpec` / `ToolHandler` / `ToolRegistry` 三件套，`to_openai_function()` 自动生成 schema，异常与截断在 `execute()` 统一收口 |
| **密钥检测真实且已接入主循环** | `security_scanner.py` 14 条正则 + 严重级别；`nodes.py:258-267` 在每次 `apply_patch` / `file_rw` 成功后自动扫描并回注结果（实测命中 7 条） |
| **多供应商抽象合理** | 7 个 `ProviderKind` / 3 套原生协议实现（OpenAI / Claude / Ollama），`endpoint` 属性集中处理 6 种 URL 规则（含 Azure 的 deployment 路径 + api-version），错误归一化为可重试/不可重试 |
| **RAG 检索算法是标准做法** | 手写 BM25（k1=1.5 / b=0.75，完整 IDF 与文档长度归一化）；RRF 融合 k=60；CrossEncoder 重排；tree-sitter 按函数/类 AST 切块（Python / TS / TSX / JS 为真 AST） |
| **事件系统完整** | 62 个事件常量 + 28 个发射方法；同步/流式双入口通过同一批节点函数驱动 |
| **Electron 安全基线正确** | `nodeIntegration: false` + `contextIsolation: true`；BrowserView 额外启用 `sandbox: true` |
| **敏感信息未泄漏** | `/config` 返回的 `api_key` 已脱敏为 `***`；`.gitignore` 正确排除 `aurora.json` 与 `.env` |

---

## 附录 A：路径越权修复的实测记录

**攻击构造**（工作区 `sandbox_demo_ws`，攻击目标为同级前缀目录 `sandbox_demo_ws-evil`）：

| 用例 | 结果 |
|---|---|
| 正常：工作区内文件 `notes.txt` | ✅ 放行 |
| 攻击：同级前缀目录 `..\sandbox_demo_ws-evil\secret.txt` | ✅ **PermissionError 拦截** |
| 攻击：多级上跳 `..\..\Windows\System32\drivers\etc\hosts` | ✅ **PermissionError 拦截** |
| 正常：工作区本身 `.` | ✅ 放行 |

**新旧写法对比**：

```
workspace      = D:\codex_Projects\Aurora\sandbox_demo_ws
resolved       = D:\codex_Projects\Aurora\sandbox_demo_ws-evil\secret.txt
startswith(ws) = True    ← 旧写法误判为「在内」，放行（漏洞本体）
is_relative_to = False   ← 新写法正确判定为「在外」，拦截
```

## 附录 B：关键数字速查

> **更正说明（2026-09-13 复测）**：本表原有多行实际是 `llm_providers.py` 的**行号引用**，
> 被误当作「数量」写入。经逐项复核，`Provider 种类 / 原生协议实现 = 7 / 3`、
> `密钥检测正则数 = 14 条`、`风险等级 / 审批策略 = 4 / 4` 三行均为行号误植，其「数量」列无意义，已删除并替换为实测计数。
> 其余保留项均已重新测量，测量方式见备注。

| 项 | 数值 | 测量方式 / 备注 |
|---|---|---|
| Python 文件 / 行数 | 216 个 / 40,739 行 | Python `os.walk`（排除 `node_modules`、`__pycache__`）；含并发编辑产生的临时文件。git 已跟踪 = 212 个 / 39,645 行 |
| 路由对象 / OpenAPI 路径 | 267 / 243 | 导入 `backend.api` 后遍历 `app.routes`；243 = 去重路径数（原「241」应为笔误） |
| 工具数 / 分类数 | 29 / 20 | `tool_registry.list_tools()` 运行时枚举 |
| Provider 种类（`ProviderKind` 枚举成员） | 7 | `llm_providers.py:12-19` 实枚举：OPENAI / CLAUDE / OLLAMA / OPENROUTER / AZURE / DEEPSEEK / CUSTOM。**此前的「7 / 3」中「3」是行号误植，已删** |
| Provider 原生协议实现类 | 3 | `OpenAIProvider` / `ClaudeProvider` / `OllamaProvider`（其余 4 种 kind 复用 OpenAI 兼容实现） |
| SSE 事件常量 / 发射方法 | 62 / 28 | `len(list(SSEEventType))` = 62；`SSEBus` 发射方法 = 28（原「28」正确） |
| 测试用例（收集数） | 516 | `python -m pytest tests/ -q` → `516 passed`（2026-09-13 复测；并发修复新增用例后由 446 增至 516） |
| 工具输出截断阈值 | 65536 触发 → 16384（头尾保留）→ 8000（状态内） | 同上，未复测细项 |
| 滑动窗口 | 工具选择 20 条 / 合成 15 条 / 简单对话 8 条 | 未复测 |
| 会话 Token 预算默认值 | 24000 | 未复测 |
| 历史折叠触发条件 | 消息 > 60 条 或 工具结果 > 20 个 | 未复测 |
| 密钥检测正则数 | **14 条** | `security_scanner.py:23-39` 的 `SECRET_PATTERNS` 长度，实数为 14。「14」本身正确，但原表将其与行号混排；已确认是计数 |
| 风险等级数 | 4 级 | `RiskLevel` 枚举：LOW / MEDIUM / HIGH / CRITICAL，实测 4 级 |
| 审批策略数 | 4 档 | `ApprovalPolicy` 枚举：NEVER / ON_FAILURE / ON_REQUEST / UNTRUSTED，实测 4 档。**原「4 级 / 4 档」两个数字恰好都对，但被标注为 `llm_providers.py` 行号，属误标** |
| 审批等待超时 | 30 秒（超时按拒绝） | 未复测 |

---

*本文档所有结论均基于源码通读与实际运行验证，每条附文件行号，可直接定位。*
