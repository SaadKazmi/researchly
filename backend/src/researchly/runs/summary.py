"""The run summary's coverage figure (spec §6.3 step 9, §10): Chao2 over the project's candidate files."""

import json
import uuid
from typing import Any

from langgraph.store.base import BaseStore

from researchly.services.identity import normalize_title
from researchly.services.ranking import chao2


async def coverage(store: BaseStore | None, project_id: uuid.UUID) -> dict[str, Any] | None:
    """One incidence list per (sub-question file, source). Files are project-wide, so earlier runs count too."""
    if store is None:
        return None
    # ponytail: one page of 1000 project files; page through asearch if a project ever holds more
    items = await store.asearch(("project", str(project_id)), limit=1000)
    lists: dict[tuple[str, str], set[str]] = {}
    for item in items:
        if not (item.key.startswith("/candidates/") and item.key.endswith(".jsonl")):
            continue
        content = item.value.get("content", "")
        text = content if isinstance(content, str) else "\n".join(content)  # deepagents' older format is a list
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                sources = record.get("extra", {}).get("found_by") or [record["source"]]
                title = normalize_title(record["title"])
            except (ValueError, KeyError, TypeError):  # one bad line must not fail the run's summary
                continue
            for source in sources:
                lists.setdefault((item.key, source), set()).add(title)
    observed, estimated = chao2(list(lists.values()))
    return {"observed": observed, "estimated": round(estimated, 1), "lists": len(lists)}
