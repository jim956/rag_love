# 恋爱小助手 API

基于 **LangGraph** 的对话式 RAG 后端：把一对情侣的聊天记录做成可检索的知识库，由带工具的 Agent 按需检索，再配合外部 MCP 工具（必应搜索、12306 车票）回答提问。接口用 **FastAPI** 提供，回答以 **SSE** 流式推送给前端。

## 功能

- **混合检索**：查询经 SiliconFlow 向量化后，在 Milvus 中同时做稠密向量 + 稀疏向量召回，再用交叉编码器重排，返回按相关度排序的聊天片段（带起止时间）。
- **Agent 循环**：`agent` 节点决定是否调用工具，`tools` 节点执行检索/外部工具，循环直到模型给出最终回答（`langgraph.prebuilt.ToolNode` + `tools_condition`）。
- **外部工具（MCP）**：通过 `npx` 以 stdio 子进程方式加载 `bing-cn-mcp`（必应搜索）和 `12306-mcp`（车票查询），服务启动时一次性建好常驻会话。
- **会话持久化**：对话历史由 LangGraph 的 `AsyncPostgresSaver` 检查点写入 PostgreSQL，`thread_id` 同时是会话主键。
- **流式输出**：`/api/chat` 以 Server-Sent Events 推送 token、工具调用状态和结束事件。

## 技术栈

| 组件 | 用途 |
| --- | --- |
| FastAPI + uvicorn | HTTP / SSE 服务 |
| LangGraph + langchain-openai | 对话图、检查点、OpenAI 兼容模型调用 |
| PostgreSQL | LangGraph 会话检查点存储 |
| Milvus | 聊天记录片段的稠密 + 稀疏混合检索与重排 |
| SiliconFlow | 文本向量化（`BAAI/bge-m3`）与重排（`BAAI/bge-reranker-v2-m3`） |
| Node.js / npx | 运行 MCP stdio 服务器 |

## 目录结构

```
.
├── main.py                 # ASGI 入口：uvicorn main:app
├── app/
│   ├── main.py             # FastAPI 应用、lifespan（连接 PostgreSQL、加载 MCP 工具、编译对话图）
│   ├── config.py           # 环境变量配置（.env）
│   ├── schemas.py          # 请求 / 响应模型
│   ├── messages.py         # 消息文本提取
│   ├── api/
│   │   ├── chat.py         # POST /api/chat（SSE 流式对话）
│   │   └── threads.py      # GET /api/threads（列表）、GET/DELETE /api/threads/{thread_id}
│   ├── graph/
│   │   ├── agent.py        # 构建 LangGraph 对话图
│   │   ├── state.py        # ChatState（messages）
│   │   ├── tools.py        # 本地工具：聊天记录检索、当前时间
│   │   └── mcp.py          # MCP 常驻会话与工具加载
│   └── services/
│       └── retrieval.py    # Milvus 混合检索 + 重排
├── Dockerfile
└── .env.example
```

## 快速开始

### 1. 准备配置

```bash
cp .env.example .env
# 填入 DASHSCOPE_API_KEY、SF_API_KEY、MILVUS_URI、POSTGRES_URI 等
```

以上四项为**必填**：任一为空时服务会在启动阶段直接抛出 `RuntimeError` 并列出缺失项。

### 2. 本地运行

需要 Python 3.11，以及 Node.js（MCP 工具依赖 `npx`）。

```bash
uv sync
uvicorn main:app --reload
```

服务默认监听 `http://127.0.0.1:8000`，交互式接口文档见 `/docs`。

### 3. Docker

镜像内已包含 Python 3.11 + uv + Node.js，并以非 root 用户 `app` 运行、暴露 `8000` 端口。

```bash
docker build -t rag-love .
docker run --env-file .env -p 8000:8000 rag-love
```

构建时会预热 npm 缓存（`npx -y bing-cn-mcp`、`npx -y 12306-mcp`）；预热失败只会打印 `warmup failed`，不影响镜像可用。需要强制重新预热时改构建参数：

```bash
docker build --build-arg NPM_WARM_EPOCH=2 -t rag-love .
```

> 容器以**单 worker** 启动：对话图和 MCP 会话挂在应用状态上，多 worker 会各自重复建立连接。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | — | **必填**，对话模型 API Key |
| `DASHSCOPE_BASE_URL` | — | 对话模型的 OpenAI 兼容接口地址 |
| `CHAT_MODEL` | `deepseek-v4.1-flash-1017` | 对话模型名 |
| `SF_API_KEY` | — | **必填**，SiliconFlow API Key（向量化 / 重排） |
| `SF_BASE_URL` | `https://api.siliconflow.cn/v1` | SiliconFlow 接口地址 |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 向量化模型 |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | 重排模型 |
| `MILVUS_URI` | — | **必填**，Milvus 地址 |
| `MILVUS_DATABASE` | `rag_love` | Milvus 数据库 |
| `MILVUS_USER` | `root` | Milvus 用户名 |
| `MILVUS_PASSWORD` | 空 | Milvus 密码 |
| `MILVUS_COLLECTION` | — | 检索使用的 collection |
| `MILVUS_DENSE_FIELD` | `float_vector` | 稠密向量字段名 |
| `MILVUS_SPARSE_FIELD` | `sparse_vector` | 稀疏向量字段名 |
| `MILVUS_RERANK_CREDENTIAL` | `siliconflow_apikey` | Milvus 中为 SiliconFlow 配置的凭据名 |
| `SEARCH_LIMIT` | `10` | 默认返回片段数 |
| `POSTGRES_URI` | — | **必填**，PostgreSQL 连接串（检查点存储） |
| `POSTGRES_POOL_MAX_SIZE` | `10` | 连接池大小 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的跨域来源，逗号分隔 |

配置从环境变量或项目根目录的 `.env` 读取。Milvus collection 需包含 `text`、`start_ts`、`end_ts` 输出字段，以及稠密/稀疏向量两个检索字段。

## API

### `POST /api/chat`

发起一轮对话，返回 `text/event-stream`。

请求体：

```json
{
  "message": "我们上次吵架是因为什么？",
  "thread_id": "demo-001"
}
```

- `message`：必填，1–4000 字符。
- `thread_id`：可选，`^[A-Za-z0-9_-]{1,64}$`；不传则由服务端生成新会话。

SSE 事件：

| 事件 | data | 说明 |
| --- | --- | --- |
| `start` | `{"thread_id": "..."}` | 流开始，带回会话 ID |
| `token` | `{"content": "..."}` | 模型输出的增量文本 |
| `tool` | `{"name": "...", "status": "..."}` | 工具调用结束 |
| `done` | `{"thread_id": "..."}` | 生成完成 |
| `error` | `{"message": "..."}` | 生成失败（随后仍会收到 `done`） |

示例：

```bash
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "帮我查一下明天北京到上海的高铁"}'
```

```
event: start
data: {"thread_id": "6f1c..."}

event: token
data: {"content": "好的，"}

event: tool
data: {"name": "get_date_and_time", "status": "success"}

event: done
data: {"thread_id": "6f1c..."}
```

### `GET /api/threads`

列出所有会话，最近的排在最前面，最多返回 50 条。

```json
{
  "threads": [
    {
      "thread_id": "6f1c...",
      "title": "帮我回忆一下我们的 2025 年…",
      "updated_at": "2026-10-30T05:27:28.528520Z"
    }
  ]
}
```

- `thread_id` 与 `updated_at` 直接取自检查点表（`checkpoints.checkpoint->>'ts'` 的最大值）。
- `title` 没有单独落库，取该会话首条用户消息的前 18 个字符；标题为空时返回“新的对话”。
- 因为要逐个读取检查点来取标题，条目多或会话很长时会慢一些，所以只列最近 50 个。

### `GET /api/threads/{thread_id}`

从 PostgreSQL 检查点读取会话历史，只返回用户与助手消息（工具消息不返回）。

```json
{
  "thread_id": "demo-001",
  "messages": [
    {"role": "user", "content": "我们上次吵架是因为什么？"},
    {"role": "assistant", "content": "……"}
  ]
}
```

### `DELETE /api/threads/{thread_id}`

删除该会话的检查点，返回 `204`。

### `GET /api/health`

健康检查，返回 `{"status": "ok"}`。

## 行为说明

- **启动流程**：连接 PostgreSQL → 幂等创建检查点表 → 逐个建立 MCP 会话并加载工具 → 编译对话图。MCP 加载失败（离线、缺少 `npx`、包名错误）只打 warning，服务仍会用本地工具正常启动。
- **MCP 超时**：单个服务器建连或拉取工具列表超过 30 秒会被跳过。首次运行需要下载 npm 包，此时重启一次即可命中缓存。
- **本地工具**：`search_chat_records`（按查询和时间范围检索聊天片段，A 表示男方、B 表示女方）与 `get_date_and_time`（当前时间）。
- **会话列表**：前端不保存任何本地状态，侧边栏完全由 `GET /api/threads` 提供；新会话的 `thread_id` 由服务端生成，通过 SSE 的 `start` 事件回传。
- **回答风格**：系统提示要求基于检索内容作答、标注时间、不编造信息，并使用标准中文 Markdown 排版。