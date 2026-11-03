"""Identity resolution (spec §8.3): exact IDs first, then title trigram ≥ 0.92 + year ± 1 + first-author surname."""

import re
import unicodedata
from dataclasses import replace

from researchly.connectors import ids as idn
from researchly.connectors.base import ExternalId, PaperRecord

TITLE_SIMILARITY = 0.92
# spec §8.1.1: OpenAlex enriches first, then Crossref; arXiv and HF have the thinnest metadata
SOURCE_PRIORITY = ("openalex", "crossref", "pubmed", "europepmc", "datacite", "arxiv", "hf", "opencitations")
MERGED_FIELDS = (
    "title",
    "abstract",
    "year",
    "authors",
    "venue",
    "venue_type",
    "cited_by",
    "fwci",
    "pct",
    "oa_url",
    "is_oa",
    "license",
    "is_retracted",
)


def normalize_title(title: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", title) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded.lower()).split())


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):  # pg_trgm: words padded with two spaces before, one after
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def trigram_similarity(a: str, b: str) -> float:
    ga, gb = _trigrams(a), _trigrams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def surname(name: str) -> str | None:
    name = name.strip()
    last = name.split(",")[0] if "," in name else name.split()[-1] if name.split() else ""
    return normalize_title(last) or None


def first_surname(record: PaperRecord) -> str | None:
    return surname(record.authors[0].name) if record.authors else None


def _ids(record: PaperRecord) -> set[ExternalId]:
    return {n for i in record.ids if (n := idn.normalize(i))}


def same_paper(a: PaperRecord, b: PaperRecord) -> bool:
    if _ids(a) & _ids(b):
        return True
    if a.year is None or b.year is None or abs(a.year - b.year) > 1:
        return False
    sa, sb = first_surname(a), first_surname(b)
    if not sa or sa != sb:
        return False
    return trigram_similarity(normalize_title(a.title), normalize_title(b.title)) >= TITLE_SIMILARITY


def source_rank(source: str) -> int:
    return SOURCE_PRIORITY.index(source) if source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)


def merge(records: list[PaperRecord]) -> PaperRecord:
    ordered = sorted(records, key=lambda r: source_rank(r.source))
    merged = replace(ordered[0], ids=[], references=[], extra={}, field_sources={})
    for name in MERGED_FIELDS:
        for record in ordered:
            value = getattr(record, name)
            if value not in (None, [], ""):
                setattr(merged, name, value)
                merged.field_sources[name] = record.source
                break
    seen_ids: set[ExternalId] = set()
    for record in ordered:
        for i in sorted(_ids(record)):
            if i not in seen_ids:
                seen_ids.add(i)
                merged.ids.append(i)
        merged.references += [r for r in record.references if r not in merged.references]
        merged.extra = {**record.extra, **merged.extra}  # the higher-priority source wins on clashes
    merged.source = ordered[0].source
    return merged


def dedupe(records: list[PaperRecord]) -> list[PaperRecord]:
    """# ponytail: O(n²) clustering; fine for one fan-out (a few hundred records)."""
    clusters: list[list[PaperRecord]] = []
    for record in records:
        hits = [c for c in clusters if any(same_paper(record, other) for other in c)]
        joined = [record] + [r for c in hits for r in c]
        clusters = [c for c in clusters if c not in hits] + [joined]
    return [merge(c) for c in clusters]
