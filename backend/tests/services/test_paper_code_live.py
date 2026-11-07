"""Nightly: the pinned PwC archive file still downloads, matches its sha256 and parses."""

import pytest

from researchly.connectors.http import PoliteClient
from researchly.services import paper_code as pc

pytestmark = [pytest.mark.nightly, pytest.mark.anyio]


async def test_the_pinned_archive_downloads_and_parses():
    async with PoliteClient() as client:
        body = await pc.download(client)
    rows = [r for item in pc.iter_links(body) if (r := pc.to_row(item))]
    assert len(rows) > 250_000
    assert any(r["arxiv_id"] == "1706.03762" and r["is_official"] for r in rows)
