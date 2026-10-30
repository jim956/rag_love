"""通过 MCP 接入外部工具（必应搜索、12306 车票）。

工具在 FastAPI 启动阶段加载一次并编译进对话图。这里为每个服务器保留一个常驻
ClientSession：`get_tools()` 是每次工具调用都新建会话（实测一次调用约 7~8 秒），
而把 session 直接交给 `load_mcp_tools` 后单次调用降到毫秒级。

会话由 lifespan 里的 `AsyncExitStack` 在服务关闭时统一释放。
代价：npx 子进程若中途退出，该服务器的工具会一直报错，需要重启服务。
"""

import asyncio
import logging
from contextlib import AsyncExitStack

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

logger = logging.getLogger(__name__)

# 单个服务器建立会话 / 拉取工具列表的超时。首次运行 npx 要下载 npm 包，
# 可能超过这个值；超时只跳过该服务器并打 warning，重启一次即可命中缓存。
MCP_LOAD_TIMEOUT_SECONDS = 30.0

mcp_client = MultiServerMCPClient(
    {
        "bing-search": {"transport": "stdio", "command": "npx", "args": ["-y", "bing-cn-mcp"]},
        "12306-mcp": {"transport": "stdio", "command": "npx", "args": ["-y", "12306-mcp"]},
        "howtocook-mcp": {"transport": "stdio", "command": "npx", "args": ["-y", "howtocook-mcp"]
                          }
    },
)


async def open_mcp_sessions(stack: AsyncExitStack) -> list[BaseTool]:
    """为每个 MCP 服务器建立常驻会话并加载工具，单个服务器失败只跳过。

    会话的关闭交给调用方传入的 `stack`（FastAPI lifespan 里的 `async with`）。
    逐个服务器建连：某个服务器超时或失败不会影响其它服务器已加载的会话。
    """
    tools: list[BaseTool] = []
    for name in mcp_client.connections:
        try:
            # 用 asyncio.timeout 而不是 wait_for：后者会把 __aenter__ 放到新 task 里执行，
            # anyio 的 cancel scope 退出时会报 "exit cancel scope in a different task"。
            async with asyncio.timeout(MCP_LOAD_TIMEOUT_SECONDS):
                session = await stack.enter_async_context(mcp_client.session(name))
                server_tools = await load_mcp_tools(session, server_name=name)
        except Exception as exc:
            logger.warning("MCP 服务器 %s 初始化失败，已跳过：%r", name, exc)
            continue

        logger.info(
            "MCP 服务器 %s 提供 %d 个工具：%s",
            name,
            len(server_tools),
            [tool.name for tool in server_tools],
        )
        tools.extend(server_tools)

    return tools
