"""HTTP 请求 / 响应模型。"""

from typing import Literal

from pydantic import BaseModel, Field

# 会话 ID 同时作为 LangGraph 的 thread_id 和 PostgreSQL 检查点主键
THREAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="用户本轮输入")
    thread_id: str | None = Field(
        default=None,
        pattern=THREAD_ID_PATTERN,
        description="会话 ID；不传则由服务端新建会话",
    )


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ThreadHistory(BaseModel):
    thread_id: str
    messages: list[HistoryMessage]