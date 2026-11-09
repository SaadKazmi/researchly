"""Test doubles for agent, tool and evidence tests: scripted chat models, local-model stand-ins and stubs."""

import hashlib
import itertools
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.connectors.base import ConnectorCaps, ExternalId, PaperRecord, SearchQuery, SourceUnavailable
from researchly.db.models import EMBED_DIM, Message, Project, Run, Thread

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


class MemoryFiles:
    """The project store as a dict, keyed by the paths tools pass (deepagents' StoreBackend in the app)."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        if file_path not in self.files:
            return SimpleNamespace(file_data=None, error=f"File '{file_path}' not found")
        return SimpleNamespace(file_data={"content": self.files[file_path]}, error=None)

    async def awrite(self, file_path: str, content: str) -> Any:
        self.files[file_path] = content
        return SimpleNamespace(error=None, path=file_path)


class StubConnector:
    def __init__(self, name: str, records: list[PaperRecord] | None = None, *, fail: bool = False) -> None:
        self.name, self.records, self.fail = name, records or [], fail
        self.queries: list[SearchQuery] = []
        self.got: list[list[ExternalId]] = []

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        self.queries.append(query)
        if self.fail:
            raise SourceUnavailable(self.name, "down")
        return list(self.records)

    async def get(self, ids: list[ExternalId], /) -> list[PaperRecord]:
        self.got.append(list(ids))
        return [r for r in self.records if set(r.ids) & set(ids)]

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=False, citations_out=False)


async def seed_run(
    sessions: async_sessionmaker[AsyncSession],
    *,
    effort: str = "standard",
    status: str = "pending",
    budget: str = "1",
    question: str = "What makes Transformers fast to train?",
    domains: tuple[str, ...] = (),
) -> Run:
    """A project, a thread, a run and the run's user message. Parents are flushed first (no relationship())."""
    project = Project(id=uuid.uuid4(), name="p", settings={"domains": list(domains)})
    thread = Thread(id=uuid.uuid4(), project_id=project.id)
    run_id = uuid.uuid4()
    run = Run(
        id=run_id,
        project_id=project.id,
        thread_id=thread.id,
        effort=effort,
        status=status,
        budget_usd=Decimal(budget),
        spent_usd=Decimal(0),
        checkpoint_thread_id=str(run_id),
    )
    async with sessions() as session, session.begin():
        for row in (project, thread, run):
            session.add(row)
            await session.flush()
        session.add(Message(thread_id=thread.id, role="user", content={"text": question}, run_id=run.id))
    return run
