"""Builds each run's graph from the configured providers (spec §6.1, §7.1). HTTP clients and local models are shared.

Keys are read from the keychain inside chat_model, at build time, and never stored here (AGENTS.md).
"""

import asyncio
import dataclasses
from functools import partial
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.agents.context import EFFORTS
from researchly.agents.factory import build_agent
from researchly.agents.tools import ToolDeps
from researchly.config import Settings
from researchly.connectors import build_connectors
from researchly.connectors.cache import HttpCache
from researchly.connectors.http import PoliteClient
from researchly.connectors.robots import Robots
from researchly.connectors.sidecar import SidecarClient
from researchly.db.models import ProviderConfig, Run
from researchly.embeddings import Embedder, Reranker
from researchly.llm.ledger import attach_ledger, spent_usd
from researchly.llm.registry import ProviderSpec, Role, chat_model
from researchly.services.fulltext import FullTextFetcher


class LiveRuntime:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        *,
        checkpointer: BaseCheckpointSaver,
        store: BaseStore,
        searxng: SidecarClient | None,
    ) -> None:
        self.settings, self.sessions = settings, sessions
        self.checkpointer, self.store, self.searxng = checkpointer, store, searxng
        self._lock = asyncio.Lock()
        self._client: PoliteClient | None = None
        self._base: ToolDeps | None = None

    async def build(self, run: Run) -> Any:
        async with self.sessions() as session:
            rows = (await session.scalars(select(ProviderConfig))).all()
        providers = [ProviderSpec(r.provider, r.key_ref, r.base_url, r.models) for r in rows]

        def model(role: Role) -> BaseChatModel:
            return attach_ledger(chat_model(role, providers, self.settings), self.sessions, run_id=run.id, role=role)

        lead, mid, cheap = model("strong"), model("mid"), model("cheap")  # NoModelForRole before anything loads
        return build_agent(
            EFFORTS[run.effort],
            lead=lead,
            scout=cheap,
            reader=mid,
            deps=await self._deps(mid),
            spent=partial(spent_usd, self.sessions),
            checkpointer=self.checkpointer,
            store=self.store,
        )

    async def _deps(self, extractor: BaseChatModel) -> ToolDeps:
        async with self._lock:
            if self._base is None:
                models = self.settings.data_dir / "models" / "fastembed"
                embedder = await asyncio.to_thread(Embedder, models)  # downloads on first use
                reranker = await asyncio.to_thread(Reranker, models)
                self._client = client = PoliteClient(cache=HttpCache(self.sessions))
                self._base = ToolDeps(
                    sessions=self.sessions,
                    connectors=build_connectors(client),
                    searxng=self.searxng,
                    fetcher=FullTextFetcher(client, Robots(client)),
                    embedder=embedder,
                    reranker=reranker,
                    extractor=extractor,
                    spent=partial(spent_usd, self.sessions),
                )
        return dataclasses.replace(self._base, extractor=extractor)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
