from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import re
from typing import Any

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from .config import settings


def lc_messages(messages: list[dict]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for message in messages:
        role = message.get("role", "user")
        if role == "assistant":
            role = "ai"
        if role not in {"system", "user", "ai", "human"}:
            role = "user"
        normalized.append((role, str(message.get("content", ""))))
    return normalized


def _message_role(message: BaseMessage) -> str:
    if message.type == "human":
        return "user"
    if message.type == "ai":
        return "assistant"
    if message.type == "system":
        return "system"
    return "user"


class OpenAICompatibleChatModel(BaseChatModel):
    """LangChain ChatModel wrapper for any OpenAI-compatible chat endpoint."""

    temperature: float = 0.4
    timeout: int = 45

    @property
    def _llm_type(self) -> str:
        return "openai-compatible-httpx"

    def _payload(self, messages: list[BaseMessage], stop: list[str] | None = None) -> dict:
        cfg = settings()
        payload: dict[str, Any] = {
            "model": cfg["llm_model"],
            "messages": [
                {"role": _message_role(message), "content": str(message.content)}
                for message in messages
            ],
            "temperature": self.temperature,
        }
        if stop:
            payload["stop"] = stop
        return payload

    def _headers(self) -> dict[str, str]:
        cfg = settings()
        return {"Authorization": f"Bearer {cfg['llm_api_key']}"}

    def _endpoint(self) -> str:
        return f"{settings()['llm_base_url'].rstrip('/')}/v1/chat/completions"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        cfg = settings()
        if not cfg["llm_api_key"]:
            raise RuntimeError("LLM_API_KEY is not configured")
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self._endpoint(), headers=self._headers(), json=self._payload(messages, stop))
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        cfg = settings()
        if not cfg["llm_api_key"]:
            raise RuntimeError("LLM_API_KEY is not configured")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self._endpoint(), headers=self._headers(), json=self._payload(messages, stop))
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


class OpenAICompatibleEmbeddings(Embeddings):
    def __init__(self, model: str, api_key: str, base_url: str, timeout: int = 45):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not self.model or not self.api_key:
            raise RuntimeError("Embedding API is not configured")
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda item: item["index"])
            return [row["embedding"] for row in rows]

    async def _aembed(self, texts: list[str]) -> list[list[float]]:
        if not self.model or not self.api_key:
            raise RuntimeError("Embedding API is not configured")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda item: item["index"])
            return [row["embedding"] for row in rows]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._aembed(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._aembed([text]))[0]


class HashingEmbeddings(Embeddings):
    """Local deterministic embeddings for demos when no embedding API is available."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def _vectorize(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        if not tokens:
            tokens = [text[:128] or "empty"]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)


@lru_cache
def chat_model() -> OpenAICompatibleChatModel:
    return OpenAICompatibleChatModel()


@lru_cache
def embedding_model() -> Embeddings:
    cfg = settings()
    if cfg["embedding_model"]:
        return OpenAICompatibleEmbeddings(
            model=cfg["embedding_model"],
            api_key=cfg["embedding_api_key"],
            base_url=cfg["embedding_base_url"],
        )
    return HashingEmbeddings(cfg["embedding_dimensions"])


def chat_chain(messages: list[dict]):
    prompt = ChatPromptTemplate.from_messages(lc_messages(messages))
    return prompt | chat_model() | StrOutputParser()


def rag_answer_chain(system_prompt: str):
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "可参考知识库：\n{context}\n\n当前问题：{question}"),
        ]
    )
    return (
        {
            "context": RunnableLambda(lambda item: item["context"]),
            "question": RunnableLambda(lambda item: item["question"]),
        }
        | prompt
        | chat_model()
        | StrOutputParser()
    )


def dumps_vector(values: list[float]) -> str:
    return json.dumps(values, separators=(",", ":"))


def loads_vector(value: str) -> list[float]:
    return [float(item) for item in json.loads(value)]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    total = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return total / (left_norm * right_norm)
