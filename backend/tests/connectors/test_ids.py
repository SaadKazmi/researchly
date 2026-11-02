import pytest

from researchly.connectors import ids
from researchly.connectors.base import ExternalId


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1038/nature14539", "10.1038/nature14539"),
        ("https://doi.org/10.48550/arXiv.1706.03762", "10.48550/arxiv.1706.03762"),
        ("doi:10.1000/ABC", "10.1000/abc"),
        ("not a doi", None),
    ],
)
def test_doi(raw, expected):
    assert ids.doi(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1706.03762v7", "1706.03762"),
        ("http://arxiv.org/abs/2610.41932v1", "2610.41932"),  # 5-digit new-style IDs exist now
        ("arXiv:hep-th/9901001", "hep-th/9901001"),
        ("math.AG/0101001v2", "math.AG/0101001"),
        ("abc", None),
    ],
)
def test_arxiv(raw, expected):
    assert ids.arxiv(raw) == expected


def test_arxiv_doi_is_derived_locally_and_lowercased():
    assert ids.arxiv_doi("1706.03762") == "10.48550/arxiv.1706.03762"
    assert ids.arxiv_doi("hep-th/9901001") == "10.48550/arxiv.hep-th/9901001"


@pytest.mark.parametrize(
    ("eid", "expected"),
    [
        (ExternalId("openalex", "https://openalex.org/W2626778328"), ExternalId("openalex", "W2626778328")),
        (ExternalId("pmid", "https://pubmed.ncbi.nlm.nih.gov/26017442"), ExternalId("pmid", "26017442")),
        (ExternalId("pmcid", "pmc4561234"), ExternalId("pmcid", "PMC4561234")),
        (ExternalId("doi", "garbage"), None),
    ],
)
def test_normalize(eid, expected):
    assert ids.normalize(eid) == expected


def test_record_exposes_ids_by_scheme():
    from researchly.connectors.base import PaperRecord

    rec = PaperRecord(source="arxiv", ids=[ExternalId("arxiv", "1706.03762")], title="Attention")
    assert rec.id("arxiv") == "1706.03762"
    assert rec.id("doi") is None
