"""流式对话接口：以 Server-Sent Events 推送 token、工具调用和结束事件。"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from app.messages import message_text
from app.schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # 避免 Nginx 等反向代理缓冲事件流
    "X-Accel-Buffering": "no",
}


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _tool_events(update: dict[str, Any]) -> list[str]:
    """把 tools 节点的执行结果转成前端可展示的事件。"""
    events: list[str] = []
    for node, node_update in update.items():
        if node != "tools" or not isinstance(node_update, dict):
            continue
        for message in node_update.get("messages", []):
            if isinstance(message, ToolMessage):
                events.append(_sse("tool", {"name": message.name, "status": message.status}))
    return events


async def _stream_chat(
    graph: CompiledStateGraph, thread_id: str, message: str
) -> AsyncIterator[str]:
    config = {"configurable": {"thread_id": thread_id}}
    yield _sse("start", {"thread_id": thread_id})
    try:
        async for mode, chunk in graph.astream(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                token, metadata = chunk
                if metadata.get("langgraph_node") != "agent":
                    continue
                text = message_text(token)
                if text:
                    yield _sse("token", {"content": text})
            else:
                for event in _tool_events(chunk):
                    yield event
    except asyncio.CancelledError:
        # 客户端断开连接，让检查点保留已完成的内容即可
        raise
    except Exception as exc:
        logger.exception("对话生成失败 thread_id=%s", thread_id)
        yield _sse("error", {"message": f"生成失败：{exc}"})
    yield _sse("done", {"thread_id": thread_id})


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    thread_id = payload.thread_id or uuid4().hex
    return StreamingResponse(
        _stream_chat(request.app.state.graph, thread_id, payload.message),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )