"""The agent's state graph: a standard tool-calling ReAct-style loop built
with LangGraph, using the locally-hosted Ollama model and the tools in
tools.py. Kept intentionally simple — extend with more nodes (e.g. a
separate 'approval' node gating Slack posts) as your workflow matures.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, SystemMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.config import get_config
from src.agent.planner import PLANNER_SYSTEM_PROMPT
from src.agent import tools as tool_impls


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _build_langchain_tools():
    """Wraps the plain-Python functions currently enabled in config.yaml
    (see tools.enabled_tools) with @tool decorators so LangGraph/LangChain
    can expose them to the model. Integrations that aren't configured yet
    simply don't appear here — the model won't be offered a tool it can't
    use."""
    wrapped = []
    for name, fn in tool_impls.enabled_tools().items():
        wrapped.append(tool(fn, name_or_callable=name))
    return wrapped


def build_agent_graph():
    cfg = get_config().llm
    llm_tools = _build_langchain_tools()

    llm = ChatOllama(
        base_url=cfg.host,
        model=cfg.model,
        temperature=cfg.temperature,
    ).bind_tools(llm_tools)

    def call_model(state: AgentState):
        messages = [SystemMessage(content=PLANNER_SYSTEM_PROMPT)] + state["messages"]
        response = llm.invoke(messages)
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(llm_tools))

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


# Lazily built singleton so `src.main` doesn't pay graph-construction cost
# (and doesn't need a live Ollama connection) at import time.
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent_graph()
    return _agent


def reset_agent():
    """Forces the next get_agent() call to rebuild the graph — picks up any
    integrations enabled/disabled since the last build. Called by
    POST /admin/reload-config."""
    global _agent
    _agent = None


def run_plan_request(user_request: str) -> str:
    agent = get_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": user_request}]})
    return result["messages"][-1].content
