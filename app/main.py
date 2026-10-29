"""FastAPI 应用：启动时连接 PostgreSQL 并把检查点交给 LangGraph。"""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api import chat, threads
from app.config import get_settings
from app.graph.agent import build_graph
from app.graph.mcp import open_mcp_sessions
from app.graph.tools import LOCAL_TOOLS

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.require_runtime_keys()

    pool = AsyncConnectionPool(
        conninfo=settings.postgres_uri,
        max_size=settings.postgres_pool_max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        await pool.open(wait=True)
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()  # 幂等地创建检查点所需的数据表
        app.state.checkpointer = checkpointer

        # MCP 的常驻会话挂在 exit stack 上，随服务关闭一起释放
        async with AsyncExitStack() as stack:
            tools = list(LOCAL_TOOLS)
            try:
                tools.extend(await open_mcp_sessions(stack))
            except Exception:
                # npx 缺失、离线、包名写错都不应让整个 API 启动失败
                logger.warning("MCP 工具加载失败，仅使用本地工具", exc_info=True)

            app.state.graph = build_graph(checkpointer, tools=tools)
            logger.info(
                "PostgreSQL 检查点就绪，对话图已编译（本地 %d 个工具，MCP %d 个）",
                len(LOCAL_TOOLS),
                len(tools) - len(LOCAL_TOOLS),
            )
            yield
    finally:
        await pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="恋爱小助手 API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(threads.router, prefix="/api", tags=["threads"])

    @app.get("/api/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()