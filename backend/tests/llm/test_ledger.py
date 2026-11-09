import uuid
from decimal import Decimal

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import LlmCall
from researchly.llm.ledger import LedgerCallback, attach_ledger, spent_usd

pytestmark = [pytest.mark.db, pytest.mark.anyio]


async def test_ledger_writes_usage_and_cost(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    sessions = make_sessionmaker(engine)
    run_id = uuid.uuid4()
    reply = AIMessage(
        content="hi",
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_tokens": 1200,
            "input_token_details": {"cache_read": 400},
        },
    )
    model = GenericFakeChatModel(messages=iter([reply]))
    cb = LedgerCallback(
        sessions,
        research_run_id=run_id,
        agent="lead",
        role="mid",
        provider="anthropic",
        model="anthropic/claude-sonnet-5-5",
    )
    await model.ainvoke("hello", config={"callbacks": [cb]})
    async with sessions() as s:
        row = (await s.scalars(select(LlmCall))).one()
    await engine.dispose()
    assert (row.run_id, row.agent, row.role, row.status) == (run_id, "lead", "mid", "ok")
    assert (row.in_tok, row.cached_tok, row.out_tok) == (1000, 400, 200)
    assert row.cost_usd == Decimal("0.003240")
    assert row.latency_ms >= 0


class _FailingChatModel(GenericFakeChatModel):
    def _generate(self, *args, **kwargs):
        raise RuntimeError("provider down")


async def test_ledger_writes_zero_token_error_row(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    sessions = make_sessionmaker(engine)
    model = _FailingChatModel(messages=iter([]))
    cb = LedgerCallback(
        sessions,
        research_run_id=None,
        agent="lead",
        role="mid",
        provider="anthropic",
        model="anthropic/claude-sonnet-5-5",
    )
    with pytest.raises(RuntimeError, match="provider down"):
        await model.ainvoke("hello", config={"callbacks": [cb]})
    async with sessions() as s:
        row = (await s.scalars(select(LlmCall))).one()
    await engine.dispose()
    assert (row.status, row.in_tok, row.cached_tok, row.out_tok, row.cost_usd) == ("error", 0, 0, 0, Decimal("0"))


async def test_attached_ledger_records_the_calling_agent(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    sessions = make_sessionmaker(engine)
    run_id = uuid.uuid4()
    model = attach_ledger(
        GenericFakeChatModel(messages=iter([AIMessage("a"), AIMessage("b")])), sessions, run_id=run_id, role="cheap"
    )
    await model.ainvoke("hi", config={"metadata": {"lc_agent_name": "scout"}})
    await model.ainvoke("hi")
    async with sessions() as session:
        rows = (await session.scalars(select(LlmCall).where(LlmCall.run_id == run_id))).all()
    await engine.dispose()
    assert sorted((r.agent, r.role, r.provider, r.cost_usd) for r in rows) == [
        ("cheap", "cheap", "GenericFakeChatModel", Decimal(0)),
        ("scout", "cheap", "GenericFakeChatModel", Decimal(0)),
    ]


async def test_spent_usd_sums_one_run(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    sessions = make_sessionmaker(engine)
    run_id = uuid.uuid4()

    def row(run: uuid.UUID, cost: str) -> LlmCall:
        return LlmCall(
            run_id=run,
            agent="lead",
            role="strong",
            provider="anthropic",
            model="anthropic/claude-sonnet-5-5",
            in_tok=1,
            cached_tok=0,
            out_tok=1,
            cost_usd=Decimal(cost),
            latency_ms=1,
            status="ok",
        )

    async with sessions() as session, session.begin():
        session.add_all([row(run_id, "0.25"), row(run_id, "0.5"), row(uuid.uuid4(), "9")])
    assert await spent_usd(sessions, run_id) == Decimal("0.75")
    assert await spent_usd(sessions, uuid.uuid4()) == 0
    await engine.dispose()
