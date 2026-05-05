from __future__ import annotations

from typing import TypedDict

from .llm import call_llm, demo_response
from .rag import search_knowledge


class AgentState(TypedDict, total=False):
    user_id: int
    messages: list[dict]
    query: str
    summary: str
    rag_docs: list[dict]
    requirements: str
    plan: str
    report: str
    critique: str
    answer: str
    trace: list[dict]


def mark(state: AgentState, agent: str, output: str) -> AgentState:
    state.setdefault("trace", []).append({"agent": agent, "output": output[:220]})
    return state


async def supervisor(state: AgentState) -> AgentState:
    return mark(state, "Supervisor", "识别任务类型，调度 Analyzer、RAG、Requirement、Planner、Writer、Critic。")


async def analyzer(state: AgentState) -> AgentState:
    text = "\n".join(m["content"] for m in state.get("messages", [])[-8:])
    state["summary"] = f"会议上下文摘要：{text[-600:]}"
    return mark(state, "Analyzer", state["summary"])


async def rag_agent(state: AgentState) -> AgentState:
    docs = search_knowledge(state["user_id"], state.get("query", ""))
    state["rag_docs"] = docs
    return mark(state, "RAG Agent", f"检索到 {len(docs)} 条知识库片段。")


async def requirement(state: AgentState) -> AgentState:
    docs = "\n".join(d["content"][:300] for d in state.get("rag_docs", []))
    state["requirements"] = demo_response(
        [{"role": "user", "content": state.get("summary", "") + "\n知识库：\n" + docs}],
        "summary",
    )
    return mark(state, "Requirement", state["requirements"])


async def planner(state: AgentState) -> AgentState:
    state["plan"] = "任务拆解：用户系统、会话记录、RAG 知识库、多 Agent 编排、报告导出、管理后台。"
    return mark(state, "Planner", state["plan"])


async def writer(state: AgentState) -> AgentState:
    prompt = [
        {"role": "system", "content": "你是高级产品经理，请输出结构清晰的项目需求分析报告。"},
        {"role": "user", "content": f"{state.get('summary','')}\n{state.get('requirements','')}\n{state.get('plan','')}"},
    ]
    state["report"] = await call_llm(prompt, "report")
    return mark(state, "Writer", "完成 Markdown 报告初稿。")


async def critic(state: AgentState) -> AgentState:
    required = ["背景", "目标", "功能列表", "用户故事", "技术方案", "风险分析", "任务拆解"]
    missing = [item for item in required if item not in state.get("report", "")]
    state["critique"] = "质量校验通过。" if not missing else f"建议补充：{', '.join(missing)}"
    state["answer"] = state.get("report", "")
    return mark(state, "Critic", state["critique"])


async def run_workflow(user_id: int, messages: list[dict], query: str, mode: str = "chat") -> AgentState:
    state: AgentState = {"user_id": user_id, "messages": messages, "query": query, "trace": []}
    try:
        from langgraph.graph import END, StateGraph

        graph = StateGraph(AgentState)
        graph.add_node("Supervisor", supervisor)
        graph.add_node("Analyzer", analyzer)
        graph.add_node("RAG", rag_agent)
        graph.add_node("Requirement", requirement)
        graph.add_node("Planner", planner)
        graph.add_node("Writer", writer)
        graph.add_node("Critic", critic)
        graph.set_entry_point("Supervisor")
        graph.add_edge("Supervisor", "Analyzer")
        graph.add_edge("Analyzer", "RAG")
        graph.add_edge("RAG", "Requirement")
        graph.add_edge("Requirement", "Planner")
        graph.add_edge("Planner", "Writer")
        graph.add_edge("Writer", "Critic")
        graph.add_edge("Critic", END)
        app = graph.compile()
        return await app.ainvoke(state)
    except Exception:
        for step in [supervisor, analyzer, rag_agent, requirement, planner, writer, critic]:
            state = await step(state)
        return state
