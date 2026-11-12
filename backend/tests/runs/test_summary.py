import json
import uuid

import pytest
from langgraph.store.memory import InMemoryStore

from researchly.runs.summary import coverage

pytestmark = pytest.mark.anyio


def lines(*records: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


async def test_coverage_is_chao2_over_sub_question_and_source_lists():
    store, project = InMemoryStore(), uuid.uuid4()
    ns = ("project", str(project))
    q1 = lines(
        {"title": "Attention Is All You Need", "source": "openalex", "extra": {"found_by": ["arxiv", "openalex"]}},
        {"title": "BERT", "source": "openalex", "extra": {"found_by": ["openalex"]}},
        {"title": "GPT", "source": "arxiv", "extra": {}},  # written before found_by: its source counts
    )
    await store.aput(ns, "/candidates/q1.jsonl", {"content": "not json\n" + q1})  # a bad line is skipped
    legacy = [json.dumps({"title": "attention is all you need!", "source": "crossref", "extra": {}})]
    await store.aput(ns, "/candidates/q2.jsonl", {"content": legacy})  # deepagents' older list-of-lines format
    await store.aput(ns, "/evidence/p1.json", {"content": "{}"})
    other = lines({"title": "Other", "source": "openalex", "extra": {}})
    await store.aput(("project", str(uuid.uuid4())), "/candidates/q1.jsonl", {"content": other})
    # lists: (q1, arxiv) {A, GPT}, (q1, openalex) {A, BERT}, (q2, crossref) {A}; q1=2, q2=0 -> 3 + 2/3 * 1
    assert await coverage(store, project) == {"observed": 3, "estimated": 3.7, "lists": 3}


async def test_no_single_bad_line_can_fail_the_summary():
    store, project = InMemoryStore(), uuid.uuid4()
    ns = ("project", str(project))
    good = {"title": "Attention", "source": "openalex", "extra": {}}
    bad = [
        "[1, 2]",  # valid JSON, not an object
        "5",
        json.dumps({"title": "T", "source": "arxiv", "extra": None}),
        json.dumps({"title": "T", "source": "arxiv", "extra": {"found_by": [["unhashable"]]}}),
        json.dumps({"title": ["not", "text"], "source": "arxiv", "extra": {}}),
    ]
    await store.aput(ns, "/candidates/q1.jsonl", {"content": "\n".join([*bad, json.dumps(good)]) + "\n"})
    # a list-format file with non-string items, a None item and a good line
    await store.aput(ns, "/candidates/q2.jsonl", {"content": [None, 5, json.dumps(good)]})
    await store.aput(ns, "/candidates/q3.jsonl", {"content": None})
    assert await coverage(store, project) == {"observed": 1, "estimated": 1.0, "lists": 2}


async def test_no_store_means_no_coverage():
    assert await coverage(None, uuid.uuid4()) is None
