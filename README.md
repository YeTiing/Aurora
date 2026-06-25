<div align="center">

<img src="https://img.shields.io/badge/version-0.2.0-8b5cf6?style=flat-square">
<img src="https://img.shields.io/badge/tests-202%2F202-brightgreen?style=flat-square">
<img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square">
<img src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square">

# Aurora

### AI 编程 Agent 引擎

*FastAPI + LangGraph · Electron 桌面 · 六步流水线 · 多Agent并行*

</div>

---

## 简介

Aurora 是一个完整的 AI 编程助手引擎，含 Python 后端 + Electron 桌面端。基于 LangGraph StateGraph 构建六步 Agent 流水线：**规划 → 工具选择 → 执行 → 观察 → 循环 → 合成**。

支持 Skill/Plugin 扩展、多 Agent 并行编排、语义记忆、RAG 代码检索、模型发现等功能。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 API Key（编辑 aurora.json 或设置环境变量）
# 默认使用 OpenAI API，也支持 DeepSeek、Claude 等

# 启动服务
python run_server.py

# 打开浏览器
# http://127.0.0.1:9876
# API 文档：http://127.0.0.1:9876/docs
```

## 项目结构

```
Aurora/
├── backend/                  # FastAPI 后端核心
│   ├── agent/                # LangGraph 六步流水线
│   │   ├── graph.py          # AgentGraph 主循环 + SSE流
│   │   ├── nodes.py          # Planner / Executor / Observer / Synthesizer
│   │   ├── llm_client.py     # LLM 客户端（多Provider复用）
│   │   ├── state.py          # AgentState 状态管理
│   │   └── checkpoint.py     # 检查点回滚
│   ├── tools/                # 17+ 工具实现
│   │   ├── shell_command.py  # 终端命令执行
│   │   ├── apply_patch.py    # Diff/Patch 应用引擎
│   │   ├── file_rw.py        # 文件读写
│   │   ├── code_search.py    # 代码搜索 (rg)
│   │   ├── git_ops.py        # Git 操作
│   │   ├── web_fetch.py      # 网页抓取
│   │   ├── browser_use.py    # 浏览器控制
│   │   └── mcp_proxy.py      # MCP 协议代理
│   ├── api/                  # 100+ REST + WebSocket 端点
│   ├── rag/                  # RAG 引擎 (AST分块 + BM25 + 向量)
│   ├── memory/               # 语义记忆 (ChromaDB)
│   ├── multi_agent/          # 多Agent并行编排器
│   ├── skills/               # Skill 热加载管理器
│   ├── plugins/              # 插件系统
│   ├── mcp_hub/              # MCP 服务管理
│   ├── config/               # 三级配置 (global < user < project)
│   ├── context/              # Token 预算追踪
│   ├── observability/        # 日志/统计/追踪
│   └── model_discovery.py    # 多Provider模型发现
├── desktop/                  # Electron + React + Vite 桌面端
│   ├── src/main/             # Electron 主进程
│   └── src/renderer/         # React 前端 (Chat / Diff / Terminal / FileTree)
├── plugins/                  # 内置插件 (auto-format)
├── skills/                   # 内置 Skill
├── tests/                    # 202 个 pytest 测试
├── aurora.json               # 项目配置
└── run_server.py             # 启动入口
```

## 核心能力

| 模块 | 说明 |
|------|------|
| **Agent 流水线** | LangGraph 六步状态图：Plan → ToolSelect → Execute → Observe → Synthesize |
| **工具生态** | 17+ 工具：Shell、Patch、Git、文件、搜索、浏览器、Web、MCP代理 |
| **RAG 检索** | AST 分块 + BM25 关键词 + 向量语义 + 重排序 |
| **多Agent并行** | 支持最多 4 个 Agent 并行编排 |
| **Skill/Plugin** | 热加载 Skill 和 Plugin 扩展 |
| **语义记忆** | SQLite 向量+关键词混合检索，支持情景记忆 + 相似任务检索 |
| **模型发现** | 多 Provider 自动发现、基准测试、推荐 |
| **Token 预算** | 会话级 Token 配额管理 |
| **目标系统** | Goal 创建 + 预算追踪 + 状态管理 |
| **检查点** | 执行回滚 / 恢复 |
| **桌面端** | Electron + React，会话管理、Diff面板、终端、文件树 |

## API 概览

100 个 REST + WebSocket 端点：

| 分组 | 端点 |
|------|------|
| **Chat** | `/chat` `/chat/stream` `/ws/{session_id}` |
| **Files** | `/files` `/files/read` `/files/write` `/files/search` `/files/upload` |
| **RAG** | `/rag/index` `/rag/search` |
| **Context** | `/context/stats` `/context/budget` |
| **Auth** | `/auth/login` `/auth/oauth/login` `/auth/status` `/auth/logout` |
| **Models** | `/models` `/models/discover` `/models/test` `/models/recommend` |
| **Sessions** | `/sessions` `/sessions/{id}/rollout` |
| **Threads** | `/threads` `/threads/{id}/fork` `/threads/{id}/archive` |
| **Checkpoint** | `/checkpoint` `/checkpoint/undo` `/checkpoint/redo` |
| **Browser** | `/browser/navigate` `/browser/screenshot` `/browser/click` |
| **Memory** | `/memory/semantic/index` `/memory/semantic/search` |
| **Marketplace** | `/marketplace` `/marketplace/install` |
| **Plugins** | `/plugins` `/plugins/{name}/load` `/plugins/{name}/reload` |
| **MCP** | `/mcp/servers` `/mcp/servers/start` |
| **Observability** | `/observability/stats` |

> 完整 API 文档：启动后访问 `/docs` 查看 Swagger UI

## 配置

`aurora.json` 三級配置，优先级：**项目 > 用户 > 全局**

```json
{
    "llm": { "model": "gpt-4o", "api_key": "", "base_url": "https://api.openai.com/v1" },
    "agent": { "max_turn_iter": 30, "max_empty_turns": 3 },
    "tools": { "timeout_sec": 30, "truncate_output": 16384 },
    "context": { "token_budget": 24000 },
    "rag": { "enabled": true, "top_k": 5 },
    "multi_agent": { "max_parallel": 4 },
    "server": { "host": "127.0.0.1", "port": 9876 }
}
```

## 测试

```bash
pytest tests/ -v
```

```
202 passed in 17s ✅
```

## 桌面端

```bash
cd desktop
npm install
npm run dev      # 开发模式
npm run build    # 构建 Electron 应用
```

## 技术栈

| 层 | 技术 |
|---|------|
| 后端框架 | FastAPI + Uvicorn |
| Agent 引擎 | LangGraph StateGraph |
| LLM | OpenAI / DeepSeek / Claude (多Provider) |
| 向量检索 | SQLite FTS5 + NumPy cosine similarity |
| 代码解析 | tree-sitter (Python / TypeScript) |
| 桌面端 | Electron + React + Vite + Tailwind + Zustand |
| 测试 | pytest (asyncio) |
| 缓存 | Redis (可选) |
| 持久化 | SQLite |
| RAG | AST分块 + BM25 + 向量 + 重排序 |

## License

MIT