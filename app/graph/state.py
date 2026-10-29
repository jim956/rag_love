"""对话图的状态定义。"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    """会话状态：只有消息列表会随检查点持久化到 PostgreSQL。"""

    messages: Annotated[list[AnyMessage], add_messages]