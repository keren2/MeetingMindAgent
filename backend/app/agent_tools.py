from __future__ import annotations

import json

from langchain_core.tools import tool

from .rag import format_documents, retriever_for_user
from .skill_loader import public_skill_summary, skill_prompt_for_persona


@tool
def retrieve_knowledge_tool(user_id: int, query: str, limit: int = 5) -> str:
    """Retrieve user-scoped knowledge snippets from the LangChain vector retriever."""
    documents = retriever_for_user(user_id, limit=limit).invoke(query)
    payload = {
        "context": format_documents(documents),
        "documents": [
            {
                "id": int(doc.metadata.get("id", 0)),
                "filename": str(doc.metadata.get("filename", "")),
                "content": doc.page_content,
                "score": float(doc.metadata.get("score", 0.0)),
            }
            for doc in documents
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


@tool
def persona_skill_tool(persona: dict) -> str:
    """Return matched local skill instructions for a persona."""
    summary = public_skill_summary(persona)
    return json.dumps(
        {
            "matched_skill": summary,
            "instruction": skill_prompt_for_persona(persona),
        },
        ensure_ascii=False,
    )
