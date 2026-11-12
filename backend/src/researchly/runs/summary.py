"""The run summary's coverage figure (spec §6.3 step 9, §10): Chao2 over the project's candidate files."""

import json
import logging
import uuid
from typing import Any

from langgraph.store.base import BaseStore

from researchly.services.identity import normalize_title
from researchly.services.ranking import chao2

logger = logging.getLogger(__name__)


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
        content = item.value.get("content")
        # deepagents' older format is a list of lines; anything unreadable contributes nothing
        if isinstance(content, list):
            content = "\n".join(c for c in content if isinstance(c, str))
        text = content if isinstance(content, str) else ""
        for line in text.splitlines():
            try:
                record = json.loads(line)
                sources = record.get("extra", {}).get("found_by") or [record["source"]]
                if not (isinstance(sources, list) and all(isinstance(s, str) for s in sources)):
                    raise TypeError("sources")
                title = normalize_title(record["title"])
                for source in sources:
                    lists.setdefault((item.key, source), set()).add(title)
            except Exception as exc:  # no single line may fail a finished run's summary
                logger.debug("skipped a candidate line in %s: %s", item.key, type(exc).__name__)
    observed, estimated = chao2(list(lists.values()))
    return {"observed": observed, "estimated": round(estimated, 1), "lists": len(lists)}
