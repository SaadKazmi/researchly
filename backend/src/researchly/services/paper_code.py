"""Papers with Code archive (spec §8.1; R-conn §7): paper -> code links from the frozen July 2025 snapshot.

CC BY-SA 4.0: shown with ATTRIBUTION; never exported, bundled or redistributed (ShareAlike applies to adapted
tables we would share). One pinned, sha256-checked file, downloaded through PoliteClient on first use.
"""

import asyncio
import gzip
import hashlib
import io
import itertools
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.connectors import ids
from researchly.connectors.base import SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.db.models import PaperCode
from researchly.services.identity import fuzzy_title_ok, normalize_title

COMMIT = "1af3a546770f9b98665523ccba4b22b4f3e18c1f"  # huggingface.co/datasets/pwc-archive/files, probed 2026-11-06
FILE = "jul-28-links-between-papers-and-code.json.gz"
URL = f"https://huggingface.co/datasets/pwc-archive/files/resolve/{COMMIT}/{FILE}"
SHA256 = "e7c60f2170880af3bd5a5b9469a951341435e4ccfd21d3d18393c52cfec24694"  # X-Linked-ETag
SIZE = 26_261_205  # X-Linked-Size
SOURCE = "pwc"
ATTRIBUTION = "Papers with Code archive (CC BY-SA 4.0)"
BATCH = 5000  # rows per INSERT: 7 columns stay under psycopg's 65,535 parameters
_BETWEEN = " \t\r\n,[]"  # what separates the array's items


@dataclass(frozen=True)
class CodeLink:
    repo_url: str
    is_official: bool
    framework: str | None
    mentioned_in_paper: bool


async def download(client: PoliteClient) -> bytes:
    # No content-type gate: the sha256 is the check. PoliteClient follows the 302 to the HF CDN with SSRF checks.
    fetched = await client.get(URL, source=SOURCE, max_bytes=SIZE + 1_000_000)
    if fetched.status != 200:
        raise SourceUnavailable(SOURCE, f"HTTP {fetched.status}")
    if hashlib.sha256(fetched.body).hexdigest() != SHA256:
        raise SourceUnavailable(SOURCE, "sha256 mismatch")
    return fetched.body


def iter_links(gz: bytes, chunk: int = 1 << 20) -> Iterator[dict[str, Any]]:
    """The array's objects one at a time: the file inflates to ~150 MB of JSON, so it is never loaded whole."""
    decoder = json.JSONDecoder()
    buf = ""
    try:
        with gzip.open(io.BytesIO(gz), "rt", encoding="utf-8") as text:
            eof = False
            while not eof:
                more = text.read(chunk)
                eof = not more
                buf += more
                pos = 0
                while True:
                    while pos < len(buf) and buf[pos] in _BETWEEN:
                        pos += 1
                    if pos == len(buf):
                        break
                    try:
                        item, pos = decoder.raw_decode(buf, pos)
                    except json.JSONDecodeError:
                        if eof:
                            raise ValueError("the links file is truncated or malformed") from None
                        break  # an item cut at the chunk edge: read more
                    if isinstance(item, dict):
                        yield item
                buf = buf[pos:]
    except (OSError, EOFError, UnicodeDecodeError) as exc:  # gzip.BadGzipFile is an OSError
        raise ValueError(f"the links file is unreadable: {type(exc).__name__}") from exc


def _web(url: Any) -> bool:
    return isinstance(url, str) and url.startswith(("https://", "http://"))  # shown as a link: never javascript:


def to_row(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not _web(item.get("paper_url")) or not _web(item.get("repo_url")):
        return None
    raw_arxiv, title = item.get("paper_arxiv_id"), item.get("paper_title")
    arxiv_id = ids.arxiv(raw_arxiv) if isinstance(raw_arxiv, str) else None
    norm = normalize_title(title) if isinstance(title, str) else ""
    title_norm = norm if fuzzy_title_ok(norm) else None  # weak titles never match (spec §8.3)
    if arxiv_id is None and title_norm is None:
        return None
    framework = item.get("framework")
    return {
        "paper_url": item["paper_url"],
        "repo_url": item["repo_url"],
        "arxiv_id": arxiv_id,
        "title_norm": title_norm,
        "is_official": item.get("is_official") is True,
        "framework": framework if isinstance(framework, str) and framework not in ("", "none") else None,
        "mentioned_in_paper": item.get("mentioned_in_paper") is True,
    }


async def load(session: AsyncSession, gz: bytes) -> int:
    rows = await asyncio.to_thread(lambda: [r for item in iter_links(gz) if (r := to_row(item))])
    for batch in itertools.batched(rows, BATCH):
        await session.execute(insert(PaperCode).values(list(batch)).on_conflict_do_nothing())
    await session.commit()
    return len(rows)


async def ensure_loaded(session: AsyncSession, client: PoliteClient) -> int:
    if await session.scalar(select(PaperCode.paper_url).limit(1)) is not None:
        return 0  # the snapshot is frozen: one load is enough
    return await load(session, await download(client))


async def lookup(session: AsyncSession, *, arxiv_id: str | None = None, title: str | None = None) -> list[CodeLink]:
    conditions = []
    if arxiv_id and (a := ids.arxiv(arxiv_id)):
        conditions.append(PaperCode.arxiv_id == a)
    if title and fuzzy_title_ok(norm := normalize_title(title)):
        conditions.append(PaperCode.title_norm == norm)  # exact normalized title: the archive has no years
    for condition in conditions:
        rows = await session.scalars(
            select(PaperCode).where(condition).order_by(PaperCode.is_official.desc(), PaperCode.repo_url)
        )
        links: dict[str, CodeLink] = {}
        for r in rows:
            links.setdefault(r.repo_url, CodeLink(r.repo_url, r.is_official, r.framework, r.mentioned_in_paper))
        if links:
            return list(links.values())
    return []
