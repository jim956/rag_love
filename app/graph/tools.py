"""对话图可用的本地工具：不依赖外部进程，MCP 工具由 app.graph.mcp 单独加载。"""

import time

from langchain_core.tools import tool

from app.services.retrieval import hybrid_search

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _format_timestamp(timestamp: float) -> str:
    return time.strftime(_TIME_FORMAT, time.localtime(timestamp))


@tool
def search_chat_records(
    query: str | list[str], start_ts: int, end_ts: int, limit: int | None = None
) -> str:
    """从聊天记录库中检索这对情侣的聊天片段。聊天记录已按时间分段，每个片段带有开始时间和结束时间。其中A表示男方、B表示女方。返回结果已按相关度从高到低排列。

    Args:
        query: 需要检索的聊天记录片段内容，可以是一条或多条查询语句。
        start_ts: 检索的聊天记录片段的起始时间戳(可选)
        end_ts: 检索的聊天记录片段的终止时间戳(可选)
        limit: 返回的聊天记录片段数量，默认取配置项 SEARCH_LIMIT。

    """
    queries = [query] if isinstance(query, str) else list(query)
    if not queries:
        return "错误：检索内容为空。"

    fragments = hybrid_search(queries, start_ts, end_ts, limit)
    if not fragments:
        return "没有检索到相关聊天记录。"

    blocks = [
        f"第{index}段，开始时间 {_format_timestamp(fragment['start_ts'])}，"
        f"结束时间 {_format_timestamp(fragment['end_ts'])}，聊天内容：\n{fragment['text']}"
        for index, fragment in enumerate(fragments, start=1)
    ]
    return "\n\n".join(blocks)


@tool
def get_date_and_time() -> str:
    """获取当前日期和时间。"""
    return f"现在是{_format_timestamp(time.time())}"


LOCAL_TOOLS = [search_chat_records, get_date_and_time]
