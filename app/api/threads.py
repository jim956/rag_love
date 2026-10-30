"""会话接口：列出所有会话、从 PostgreSQL 检查点读取历史，或删除整个会话。"""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from app.messages import message_text
from app.schemas import HistoryMessage, ThreadHistory, ThreadList, ThreadSummary

router = APIRouter()

ThreadId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]

# 列表一次最多返回的会话数，避免会话积多后首屏变慢
THREAD_LIST_LIMIT = 50
# 标题取首条用户消息的前 N 个字符
TITLE_MAX_LENGTH = 18
# 逐个读取检查点时限制并发，避免占满连接池
_TITLE_CONCURRENCY = 5

# thread_id 与最后活动时间直接取自检查点表；标题没有单独落库，见 _thread_title
_LIST_THREADS_SQL = """
SELECT thread_id, max((checkpoint ->> 'ts')::timestamptz) AS updated_at
FROM checkpoints
WHERE checkpoint_ns = '' AND checkpoint ->> 'ts' IS NOT NULL
GROUP BY thread_id
ORDER BY updated_at DESC
LIMIT %s
"""


def _make_title(text: str) -> str:
    compact = " ".join(text.split())
    if not compact:
        return "新的对话"
    suffix = "…" if len(compact) > TITLE_MAX_LENGTH else ""
    return compact[:TITLE_MAX_LENGTH] + suffix


async def _thread_title(graph: Any, thread_id: str, semaphore: asyncio.Semaphore) -> str:
    """会话标题取首条用户消息现算，因此需要逐个读取检查点状态。"""
    async with semaphore:
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    for message in snapshot.values.get("messages", []):
        if isinstance(message, HumanMessage):
            text = message_text(message).strip()
            if text:
                return _make_title(text)
    return "新的对话"


async def _list_thread_rows(checkpointer: Any) -> list[dict[str, Any]]:
    async with checkpointer.conn.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(_LIST_THREADS_SQL, (THREAD_LIST_LIMIT,))
            return await cursor.fetchall()


@router.get("/threads", response_model=ThreadList)
async def list_threads(request: Request) -> ThreadList:
    """列出所有会话，最近的排在最前面。"""
    rows = await _list_thread_rows(request.app.state.checkpointer)
    graph = request.app.state.graph
    semaphore = asyncio.Semaphore(_TITLE_CONCURRENCY)
    titles = await asyncio.gather(
        *(_thread_title(graph, row["thread_id"], semaphore) for row in rows)
    )
    return ThreadList(
        threads=[
            ThreadSummary(thread_id=row["thread_id"], title=title, updated_at=row["updated_at"])
            for row, title in zip(rows, titles, strict=True)
        ]
    )


def _to_history(messages: list[AnyMessage]) -> list[HistoryMessage]:
    history: list[HistoryMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:  # 工具消息只对模型有意义，不回传给前端
            continue
        text = message_text(message).strip()
        if text:
            history.append(HistoryMessage(role=role, content=text))
    return history


@router.get("/threads/{thread_id}", response_model=ThreadHistory)
async def get_thread(request: Request, thread_id: ThreadId) -> ThreadHistory:
    snapshot = await request.app.state.graph.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    return ThreadHistory(
        thread_id=thread_id,
        messages=_to_history(snapshot.values.get("messages", [])),
    )


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(request: Request, thread_id: ThreadId) -> None:
    await request.app.state.checkpointer.adelete_thread(thread_id)