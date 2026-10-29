# 恋爱小助手

LangGraph 架构：会话检查点保存在 PostgreSQL，FastAPI 通过 SSE 流式返回。

```
                    POST /api/chat (SSE)
┌────────────┐  ───────────────────────►  ┌──────────────┐
│   客户端    │                             │   FastAPI    │
│  SSE 流式   │  ◄── start/tool/token ───  │  /api/threads│
└────────────┘        /error/done          └──────┬───────┘
                                                  │
                                    ┌─────────────▼──────────────┐
                                    │ LangGraph: agent ⇄ tools   │
                                    │  agent → 模型（OpenAI 兼容）│
                                    │  tools → 本地工具 + MCP 工具│
                                    └─────────────┬──────────────┘
                                                  │
                              ┌───────────────────┴───────────────────┐
                              │ AsyncPostgresSaver（会话检查点，多轮记忆）│
                              └───────────────────────────────────────┘
```

本地工具是聊天记录检索（Milvus 稠密 + 稀疏混合检索、再用交叉编码器重排）和取当前时间；MCP 工具由 `npx` 启动的必应搜索、12306 车票查询提供，加载失败时只降级为本地工具。

## 目录结构

```
.
├── main.py                    # ASGI 入口：uvicorn main:app
├── app/
│   ├── main.py                # FastAPI 应用、生命周期、CORS
│   ├── config.py              # 环境变量配置（pydantic-settings）
│   ├── schemas.py             # 请求 / 响应模型
│   ├── messages.py            # 消息纯文本提取
│   ├── api/
│   │   ├── chat.py            # POST /api/chat → SSE
│   │   └── threads.py         # 会话历史 / 删除
│   ├── graph/
│   │   ├── agent.py           # LangGraph 图构建与编译
│   │   ├── mcp.py             # MCP 常驻会话（必应搜索、12306 车票）
│   │   ├── state.py           # ChatState（messages）
│   │   └── tools.py           # 本地工具：聊天记录检索、当前时间
│   └── services/
│       └── retrieval.py       # Milvus 稠密+稀疏混合检索、重排
├── .env.example
├── Dockerfile
└── docker-compose.yml         # 仅后端 API
```

## 环境要求

- Python ≥ 3.11
- Node.js ≥ 22.12（后端用 `npx` 启动 MCP 工具需要它，缺失时只保留本地工具）
- PostgreSQL：仅需一个可用的数据库（首次启动需要建表权限），检查点表由 `AsyncPostgresSaver.setup()` 自动创建
- Milvus：collection 内需包含 `text`、`start_ts`、`end_ts` 字段，以及稠密 / 稀疏向量字段（后两者字段名可配置，默认 `float_vector` / `sparse_vector`）
- SiliconFlow（向量化 / 重排）与任意 OpenAI 兼容对话模型

## 配置

```bash
cp .env.example .env
```

按需填写 `.env` 中的 `DASHSCOPE_API_KEY`、`SF_API_KEY`、`MILVUS_URI`、`POSTGRES_URI`。缺少这四项时，后端启动会直接报错并列出缺失项。

主要变量：

| 变量 | 说明 |
| --- | --- |
| `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` / `CHAT_MODEL` | 对话模型（OpenAI 兼容接口） |
| `SF_API_KEY` / `SF_BASE_URL` / `EMBEDDING_MODEL` | SiliconFlow 向量化 |
| `RERANK_MODEL` / `MILVUS_RERANK_CREDENTIAL` | 重排模型，以及在 Milvus 中为 SiliconFlow 配置的凭据名称（也可在 Milvus 侧用环境变量 `SILICONFLOW_API_KEY`） |
| `MILVUS_URI` / `MILVUS_DATABASE` / `MILVUS_COLLECTION` | Milvus 连接、数据库与集合名 |
| `MILVUS_USER` / `MILVUS_PASSWORD` | Milvus 账号 |
| `MILVUS_DENSE_FIELD` / `MILVUS_SPARSE_FIELD` | 稠密 / 稀疏向量字段名 |
| `SEARCH_LIMIT` | 每次检索返回的片段数（默认 `10`） |
| `POSTGRES_URI` | LangGraph 检查点数据库，如 `postgresql://user:pass@host:5432/db` |
| `POSTGRES_POOL_MAX_SIZE` | 检查点连接池上限，默认 `10` |
| `CORS_ORIGINS` | 允许的跨域来源，逗号分隔 |

## 启动

后端（项目根目录）：

```bash
uv sync
uv run uvicorn main:app --reload --port 8000
```

## Docker 部署（仅后端）

`Dockerfile` + `docker-compose.yml` 只跑后端 API，PostgreSQL 与 Milvus 使用外部已有实例，对宿主机暴露 `8000`。

把项目（含 `uv.lock`）拷到服务器后：

```bash
cp .env.example .env        # 填 DASHSCOPE_API_KEY / SF_API_KEY / POSTGRES_URI / MILVUS_URI / CORS_ORIGINS
docker compose up -d --build
docker compose logs -f api
```

改过 `.env` 后要 `docker compose up -d` 重建容器，`restart` 不会重新读取。

### 在 PyCharm 里一键部署

不需要先把源码 rsync 到服务器。Docker 连接选「SSH 到服务器」时，IDE 拿**本机的 docker 客户端**通过 SSH 连服务器上的 daemon，构建上下文由客户端边打包边推给 daemon（`.dockerignore` 照样生效，`.env` 不进镜像）。所以「上传源码」这一步本身就不存在，一次 ▶ 就是构建 + 重建容器。

1. `Settings → Build, Execution, Deployment → Docker` 添加 SSH 连接，主机填 **IP**（如 `10.0.0.11`）。别填 `l.local`：mDNS 名字偶尔会被代理 DNS 解析到 `198.18.x.x`，SSH 直接断开。
2. 项目里已经有一个运行配置 `Deploy API to l`（`.idea/runConfigurations/Deploy_API_to_l.xml`）：Server 选上面的连接，Command `up`，Command options `-d --build --remove-orphans`。
3. 点 ▶。

等价的手工命令（PyCharm 终端里直接跑也行）：

```bash
DOCKER_HOST=ssh://jim@10.0.0.11 docker compose up -d --build
```

改业务代码后重建只会重跑最后那层 `COPY --chown=app:app . .`（0.1s），基础镜像 / uv / 依赖 / npm 缓存全部命中构建缓存。只有改了 `pyproject.toml` / `uv.lock` 才会重新装依赖，改了 Dockerfile 里 `COPY` 之前的内容才会重跑 npm 预热。

容易踩的点：

- PG / Milvus 与 API 在同一台宿主机上时，compose 用 `network_mode: host`：容器里的 `127.0.0.1` 就是宿主机，`.env` 直接写 `127.0.0.1`，也不用改 PG 的 `listen_addresses` / `pg_hba.conf`。代价是端口映射失效（uvicorn 直接绑宿主机 8000）。若要改回 bridge 网络，删除 `network_mode: host`、启用文件里注释掉的 `ports` / `extra_hosts`，并把地址换成 `host.docker.internal`。
- `l.local` 这类 mDNS 名字、以及单字母主机名（如 `l`）在容器里解析不了，报 `failed to resolve host`，只能填 IP、真实域名或 `127.0.0.1`。
- 基础镜像都取自 Docker Hub（Python 3.11 slim + pip 装 uv）。之前用过 `ghcr.io/astral-sh/uv`，国内网络下常在建镜像时被 reset（`failed to copy: read tcp ...2606:50c0...:443`）。
- 镜像内带 Node 22，因为 MCP 工具由 `npx` 启动；构建时会预热 npm 缓存。即便如此，全新镜像的首次启动仍可能因 npx 超过 30 秒被跳过某个 MCP 服务器（日志里是 `MCP 服务器 xxx 初始化失败，已跳过`），重启一次即可命中缓存。
- 没有反向代理时，`CORS_ORIGINS` 必须包含调用方页面的完整来源（含协议与端口），否则浏览器会拦掉 SSE。
- 固定单 worker：对话图与 MCP 常驻会话挂在进程内状态上，多 worker 会重复建立连接。
- 接口没有鉴权，直接把 `8000` 暴露到公网前先加登录 / 限流。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat` | 请求体 `{ "message": "...", "thread_id": "可选" }`，返回 SSE 流 |
| `GET` | `/api/threads/{thread_id}` | 从 PostgreSQL 检查点读取会话历史 |
| `DELETE` | `/api/threads/{thread_id}` | 删除该会话的全部检查点 |
| `GET` | `/api/health` | 健康检查 |

SSE 事件：

| 事件 | 数据 | 说明 |
| --- | --- | --- |
| `start` | `{"thread_id": "..."}` | 会话开始；不传 `thread_id` 时由服务端生成 |
| `tool` | `{"name": "search_chat_records", "status": "success"}` | 工具执行结果（`status` 可能为 `error`）；`name` 还可能是 `get_date_and_time` 或 MCP 工具 |
| `token` | `{"content": "..."}` | 模型输出分片 |
| `error` | `{"message": "..."}` | 生成失败 |
| `done` | `{"thread_id": "..."}` | 本次回答结束 |

## 验证

启动后端后：

```bash
curl -s http://localhost:8000/api/health   # {"status":"ok"}
```

对话链路依赖真实的 PostgreSQL、Milvus 与 API Key，项目内没有离线冒烟测试。

## 说明

- 这是一个个人自用项目，接口没有鉴权，请勿直接暴露到公网；如需部署请先加登录 / 限流。
- 同一会话不建议并发发起多个请求。