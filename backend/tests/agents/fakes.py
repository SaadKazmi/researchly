"""Test doubles for agent, tool and evidence tests: scripted chat models, local-model stand-ins and stubs."""

import hashlib
import itertools
from typing import Any

import numpy as np
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import Field

from researchly.db.models import EMBED_DIM

_ids = itertools.count()


def _describe(tool: Any) -> tuple[str, str]:
    if isinstance(tool, dict):  # an OpenAI-format tool dict, as some middleware binds
        fn = tool.get("function", tool)
        return str(fn.get("name", "")), str(fn.get("description", ""))
    if isinstance(tool, type):  # a structured-output schema bound as a tool
        return tool.__name__, tool.__doc__ or ""
    return str(getattr(tool, "name", "")), str(getattr(tool, "description", ""))


class ToolFake(GenericFakeChatModel):
    """GenericFakeChatModel that accepts tools: it records what it was bound to and every prompt it saw."""

    seen: list[list[BaseMessage]] = Field(default_factory=list)
    bound: dict[str, str] = Field(default_factory=dict)  # tool name -> description

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        self.bound.update(_describe(t) for t in tools)
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def call(name: str, args: dict[str, Any]) -> AIMessage:
    """A scripted model turn that calls one tool."""
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"{name}-{next(_ids)}"}])


def script(*replies: AIMessage | str) -> ToolFake:
    return ToolFake(messages=iter([AIMessage(r) if isinstance(r, str) else r for r in replies]))


class FakeEmbedder:
    """Deterministic unit vectors seeded by the text's hash; counts words as tokens."""

    model_id = "fake"

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def documents(self, texts) -> np.ndarray:
        out = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "little")
            v = np.random.default_rng(seed).standard_normal(EMBED_DIM).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return np.array(out)

    def query(self, text: str) -> np.ndarray:
        return self.documents([text])[0]


class KeywordReranker:
    """Logit +5 when the passage contains the keyword, else -10 (far below every gate)."""

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword.casefold()

    def scores(self, query, passages) -> list[float]:
        return [5.0 if self.keyword in p.casefold() else -10.0 for p in passages]


class StubFetcher:
    def __init__(self, full=None) -> None:
        self.full, self.targets = full, []

    async def fetch(self, target):
        self.targets.append(target)
        return self.full
