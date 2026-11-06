"""The connector contract (spec §8.2): every source returns PaperRecords keyed by normalized external IDs."""

from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, Protocol

Scheme = Literal["doi", "arxiv", "openalex", "pmid", "pmcid", "s2", "dblp", "acl"]


class ExternalId(NamedTuple):
    scheme: Scheme
    value: str


@dataclass(frozen=True)
class Author:
    name: str
    orcid: str | None = None  # bare 0000-0000-0000-0000, no URL
    openalex_id: str | None = None  # bare A…


@dataclass
class PaperRecord:
    source: str
    ids: list[ExternalId]
    title: str
    abstract: str | None = None
    year: int | None = None
    authors: list[Author] = field(default_factory=list)
    venue: str | None = None
    venue_type: str | None = None
    cited_by: int | None = None
    fwci: float | None = None
    pct: float | None = None  # citation percentile, 0..1
    oa_url: str | None = None
    is_oa: bool | None = None
    license: str | None = None
    is_retracted: bool | None = None
    references: list[ExternalId] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # source-specific: topics, upvotes, code links
    field_sources: dict[str, str] = field(default_factory=dict)  # filled by identity.merge

    def id(self, scheme: Scheme) -> str | None:
        return next((i.value for i in self.ids if i.scheme == scheme), None)


@dataclass(frozen=True)
class SearchQuery:
    text: str
    limit: int = 25
    year_from: int | None = None
    year_to: int | None = None


@dataclass(frozen=True)
class ConnectorCaps:
    search: bool
    get: bool
    citations_in: bool
    citations_out: bool


class Connector(Protocol):
    name: str

    async def search(self, query: SearchQuery) -> list[PaperRecord]: ...
    async def get(self, ids: list[ExternalId], /) -> list[PaperRecord]: ...
    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]: ...
    def capabilities(self) -> ConnectorCaps: ...


class ConnectorError(Exception):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class QuotaExhausted(ConnectorError):
    """A budget or rate quota is spent; other sources continue (spec §8.2)."""


class SourceUnavailable(ConnectorError):
    """Retries are exhausted or the circuit is open."""


class TooManyResults(ConnectorError):
    """An unpaged answer would be too large (e.g. OpenCitations citations of a famous paper)."""


class ResponseTooLarge(ConnectorError):
    """The body exceeded PoliteClient's size cap."""


class UnwantedContent(ConnectorError):
    """The server answered with a content type the caller did not ask for (a landing page instead of a PDF)."""
