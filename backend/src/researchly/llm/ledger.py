"""LLM call ledger (spec §7.5): one llm_call row per model call; feeds budgets and usage."""

import time
import uuid
from decimal import Decimal
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.db.models import LlmCall
from researchly.llm.registry import cost_usd


class LedgerCallback(AsyncCallbackHandler):
    raise_error = True  # a lost ledger row would break budgets; never swallow it

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        research_run_id: uuid.UUID | None,
        agent: str,
        role: str,
        provider: str,
        model: str,
    ) -> None:
        self.sessions = sessions
        self.research_run_id = research_run_id
        self.agent, self.role, self.provider, self.model = agent, role, provider, model
        self._started: dict[uuid.UUID, tuple[float, str]] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: uuid.UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        agent = (metadata or {}).get("lc_agent_name") or self.agent  # create_agent(name=...) tags each call
        self._started[run_id] = (time.perf_counter(), str(agent))

    async def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        generation = response.generations[0][0]
        message = generation.message if isinstance(generation, ChatGeneration) else None
        usage = (message.usage_metadata if isinstance(message, AIMessage) else None) or {}
        cached = (usage.get("input_token_details") or {}).get("cache_read", 0)
        await self._write(run_id, "ok", usage.get("input_tokens", 0), cached, usage.get("output_tokens", 0))

    async def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        await self._write(run_id, "error", 0, 0, 0)

    async def _write(self, lc_run_id: uuid.UUID, status: str, in_tok: int, cached: int, out_tok: int) -> None:
        started, agent = self._started.pop(lc_run_id, (time.perf_counter(), self.agent))
        cost = Decimal(str(round(cost_usd(self.model, in_tok, cached, out_tok), 6)))
        async with self.sessions() as session, session.begin():
            session.add(
                LlmCall(
                    run_id=self.research_run_id,
                    agent=agent,
                    role=self.role,
                    provider=self.provider,
                    model=self.model,
                    in_tok=in_tok,
                    cached_tok=cached,
                    out_tok=out_tok,
                    cost_usd=cost,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    status=status,
                )
            )


def attach_ledger(
    model: BaseChatModel, sessions: async_sessionmaker[AsyncSession], *, run_id: uuid.UUID, role: str
) -> BaseChatModel:
    """A copy of `model` that writes one llm_call row per call. Dev, replay and fake models price at $0."""
    name = str(getattr(model, "model_alias", None) or getattr(model, "model", None) or type(model).__name__)
    provider = name.split("/", 1)[0] if "/" in name else type(model).__name__
    ledger = LedgerCallback(sessions, research_run_id=run_id, agent=role, role=role, provider=provider, model=name)
    return model.model_copy(update={"callbacks": [ledger]})


async def spent_usd(sessions: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> Decimal:
    async with sessions() as session:
        total = await session.scalar(select(func.sum(LlmCall.cost_usd)).where(LlmCall.run_id == run_id))
    return Decimal(total or 0)
