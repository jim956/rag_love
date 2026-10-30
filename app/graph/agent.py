"""构建 LangGraph 对话图：agent 负责思考，tools 负责检索，循环直到给出最终回答。"""

from langchain_core.messages import SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.config import get_settings
from app.graph.state import ChatState
from app.graph.tools import LOCAL_TOOLS

SYSTEM_PROMPT = (
    "你是一对情侣的小助手。你可以调用工具检索他们的聊天记录片段、查询12306中的车票信息、从互联网中检索信息、解决今天吃什么的世纪难题"
    "回答与聊天记录有关的问题时请基于检索到的聊天内容，必要时标注时间；"
    "不要编造检索中不存在的信息，检索不到就如实说明。"
    "回答请使用标准中文 Markdown 排版：短段落、分点用列表，需要时可用小标题或表格；"
    "不要输出 HTML、图片或 LaTeX。"
)


def _build_model(tools: list[BaseTool]) -> Runnable:
    settings = get_settings()
    model = ChatOpenAI(
        model=settings.chat_model,
        base_url=settings.dashscope_base_url,
        api_key=settings.dashscope_api_key,
        streaming=True,
    )
    return model.bind_tools(tools)


def build_graph(
    checkpointer: BaseCheckpointSaver,
    agent_model: Runnable | None = None,
    *,
    tools: list[BaseTool] | None = None,
) -> CompiledStateGraph:
    """编译对话图。`agent_model` 已绑定工具时可直接注入（测试用），默认真实模型。

    `tools` 为空时只用本地工具；FastAPI 启动时会传入"本地 + MCP"合并后的列表。
    """
    tool_list = LOCAL_TOOLS if tools is None else tools
    model: Runnable = agent_model if agent_model is not None else _build_model(tool_list)

    async def agent_node(state: ChatState) -> dict:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = await model.ainvoke(messages)
        return {"messages": [response]}

    builder = StateGraph(ChatState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tool_list))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)