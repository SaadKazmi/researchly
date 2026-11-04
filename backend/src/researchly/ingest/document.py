"""What every full-text parser yields (spec §9.1): sections with their heading path, as untrusted text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    text: str  # paragraphs joined by a blank line


@dataclass(frozen=True)
class FullText:
    source: str  # "arxiv_html" | "jats" | "pdf"
    sections: list[Section]
    license: str | None = None


def clean(text: str) -> str:
    return " ".join(text.split())
