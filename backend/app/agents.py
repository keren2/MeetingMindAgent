from __future__ import annotations

from typing import TypedDict
import json

from .agent_tools import retrieve_knowledge_tool
from .llm import call_llm


class AgentState(TypedDict, total=False):
    user_id: int
    messages: list[dict]
    query: str
    memory: str
    summary: str
    rag_docs: list[dict]
    rag_context: str
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
    return mark(state, "Supervisor", "识别任务类型，调度 Memory、Analyzer、RAG、Requirement、Planner、Writer、Critic。")


async def memory_agent(state: AgentState) -> AgentState:
    turns = state.get("messages", [])[-10:]
    state["memory"] = "\n".join(f"{item.get('role', 'user')}：{item.get('content', '')}" for item in turns)[-2400:]
    return mark(state, "Memory", f"保留最近 {len(turns)} 轮上下文作为短期记忆。")


async def analyzer(state: AgentState) -> AgentState:
    text = state.get("memory", "")
    state["summary"] = await call_llm(
        [
            {"role": "system", "content": "你是需求分析 Analyzer Agent，请用 5 条以内总结会议上下文、目标、约束和待澄清点。"},
            {"role": "user", "content": text or state.get("query", "")},
        ],
        "summary",
    )
    return mark(state, "Analyzer", state["summary"])


async def rag_agent(state: AgentState) -> AgentState:
    payload = json.loads(
        retrieve_knowledge_tool.invoke({"user_id": state["user_id"], "query": state.get("query", ""), "limit": 5})
    )
    state["rag_context"] = payload["context"]
    state["rag_docs"] = payload["documents"]
    return mark(state, "RAG Agent", f"通过 LangChain Tool + Retriever 从向量库检索到 {len(state['rag_docs'])} 条知识库片段。")


async def requirement(state: AgentState) -> AgentState:
    state["requirements"] = await call_llm(
        [
            {"role": "system", "content": "你是 Requirement Agent，请把会议摘要和 RAG 材料转成结构化需求：业务目标、用户故事、验收标准、风险、待确认问题。"},
            {"role": "user", "content": f"会议摘要：\n{state.get('summary', '')}\n\nRAG材料：\n{state.get('rag_context', '')}"},
        ],
        "summary",
    )
    return mark(state, "Requirement", state["requirements"])


async def planner(state: AgentState) -> AgentState:
    state["plan"] = await call_llm(
        [
            {"role": "system", "content": "你是 Planning Agent，请按模块、优先级、依赖关系和交付里程碑拆解任务。"},
            {"role": "user", "content": state.get("requirements", "")},
        ],
        "summary",
    )
    return mark(state, "Planner", state["plan"])


async def writer(state: AgentState) -> AgentState:
    prompt = [
        {"role": "system", "content": "你是高级产品经理，请输出结构清晰的项目需求分析报告。"},
        {
            "role": "user",
            "content": (
                f"会议摘要：\n{state.get('summary','')}\n\n"
                f"RAG引用：\n{state.get('rag_context','')}\n\n"
                f"结构化需求：\n{state.get('requirements','')}\n\n"
                f"计划：\n{state.get('plan','')}"
            ),
        },
    ]
    state["report"] = await call_llm(prompt, "report")
    return mark(state, "Writer", "完成 Markdown 报告初稿。")


async def critic(state: AgentState) -> AgentState:
    required = ["背景", "目标", "功能列表", "用户故事", "技术方案", "风险分析", "任务拆解"]
    missing = [item for item in required if item not in state.get("report", "")]
    reflection = await call_llm(
        [
            {"role": "system", "content": "你是 Reflection/Critic Agent，请检查报告是否可执行，指出缺漏并给出一句质量结论。"},
            {"role": "user", "content": state.get("report", "")[:6000]},
        ],
        "summary",
    )
    state["critique"] = ("质量校验通过。" if not missing else f"建议补充：{', '.join(missing)}") + "\n" + reflection
    state["answer"] = state.get("report", "")
    return mark(state, "Critic", state["critique"])


async def run_workflow(user_id: int, messages: list[dict], query: str, mode: str = "chat") -> AgentState:
    state: AgentState = {"user_id": user_id, "messages": messages, "query": query, "trace": []}
    try:
        from langgraph.graph import END, StateGraph

        graph = StateGraph(AgentState)
        graph.add_node("Supervisor", supervisor)
        graph.add_node("Memory", memory_agent)
        graph.add_node("Analyzer", analyzer)
        graph.add_node("RAG", rag_agent)
        graph.add_node("Requirement", requirement)
        graph.add_node("Planner", planner)
        graph.add_node("Writer", writer)
        graph.add_node("Critic", critic)
        graph.set_entry_point("Supervisor")
        graph.add_edge("Supervisor", "Memory")
        graph.add_edge("Memory", "Analyzer")
        graph.add_edge("Analyzer", "RAG")
        graph.add_edge("RAG", "Requirement")
        graph.add_edge("Requirement", "Planner")
        graph.add_edge("Planner", "Writer")
        graph.add_edge("Writer", "Critic")
        graph.add_edge("Critic", END)
        app = graph.compile()
        return await app.ainvoke(state)
    except Exception:
        for step in [supervisor, memory_agent, analyzer, rag_agent, requirement, planner, writer, critic]:
            state = await step(state)
        return state
