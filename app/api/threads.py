"""会话接口：从 PostgreSQL 检查点读取历史，或删除整个会话。"""

from typing import Annotated

from fastapi import APIRouter, Path, Request
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from app.messages import message_text
from app.schemas import HistoryMessage, ThreadHistory

router = APIRouter()

ThreadId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


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