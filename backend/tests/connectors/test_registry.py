from researchly.connectors import build_connectors
from researchly.connectors.http import PoliteClient

NAMES = {
    "openalex",
    "arxiv",
    "crossref",
    "opencitations",
    "datacite",
    "europepmc",
    "pubmed",
    "hf",
    "biorxiv",
    "openreview",
    "dblp",
    "openaire",
}
SEARCH = {"openalex", "arxiv", "crossref", "europepmc", "pubmed", "hf", "openaire"}
CITES_IN = {"openalex", "opencitations", "europepmc", "pubmed"}
CITES_OUT = {"openalex", "crossref", "opencitations", "europepmc", "pubmed"}


def test_registry_holds_every_source():
    connectors = build_connectors(PoliteClient())
    assert set(connectors) == NAMES
    for name, c in connectors.items():
        assert c.name == name
        assert all(callable(getattr(c, m)) for m in ("search", "get", "citations", "capabilities"))
        caps = c.capabilities()
        assert caps.search == (name in SEARCH)
        assert caps.citations_in == (name in CITES_IN)
        assert caps.citations_out == (name in CITES_OUT)
