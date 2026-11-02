import uuid
from decimal import Decimal

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import LlmCall
from researchly.llm.ledger import LedgerCallback

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
