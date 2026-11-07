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
        ("\u200b 10.1000/abc\ufeff", "10.1000/abc"),
        ("10.1000/abc.,;", "10.1000/abc"),
        ("10.1000/abc)", "10.1000/abc"),
        ("10.1016/s0140-6736(97)11096-0", "10.1016/s0140-6736(97)11096-0"),
        ("10.1000/x(1)", "10.1000/x(1)"),
        ("https://doi.org/10.1000/abc?utm=1#frag", "10.1000/abc"),
        ("10.1000/a?b#c", "10.1000/a?b#c"),
        ("10.1234/../../works/10.5555/real", None),  # dot segments would reach another API path
        ("10.1234/a/./b", None),
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
        ("math.AG/0101001v2", "math/0101001"),
        ("MATH.AG/0101001", "math/0101001"),
        ("HEP-TH/9901001", "hep-th/9901001"),
        ("https://arxiv.org/pdf/1706.03762v7.pdf", "1706.03762"),
        ("https://www.arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://export.arxiv.org/abs/1706.03762", "1706.03762"),
        ("1706.03762V7", "1706.03762"),
        ("arXiv:1706.03762 [cs.CL]", "1706.03762"),
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
        (ExternalId("pmid", "PMID: 123"), ExternalId("pmid", "123")),
        (ExternalId("pmid", "0123"), ExternalId("pmid", "123")),
        (ExternalId("pmid", "000"), None),
        (ExternalId("pmcid", "PMCID: PMC1"), ExternalId("pmcid", "PMC1")),
        (
            ExternalId("pmcid", "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4561234/"),
            ExternalId("pmcid", "PMC4561234"),
        ),
        (ExternalId("pmcid", "https://pmc.ncbi.nlm.nih.gov/articles/PMC4561234"), ExternalId("pmcid", "PMC4561234")),
        (ExternalId("openalex", "https://openalex.org/works/W2626778328"), ExternalId("openalex", "W2626778328")),
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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://doi.org/10.1038/nature14539", ExternalId("doi", "10.1038/nature14539")),
        ("http://dx.doi.org/10.1016/S0140-6736(97)11096-0", ExternalId("doi", "10.1016/s0140-6736(97)11096-0")),
        ("https://oadoi.org/10.48550/arXiv.1706.03762", ExternalId("doi", "10.48550/arxiv.1706.03762")),
        ("https://doi.org/10.1000%2Fabc", ExternalId("doi", "10.1000/abc")),
        ("https://arxiv.org/abs/1706.03762v7", ExternalId("arxiv", "1706.03762")),
        ("https://arxiv.org/pdf/1706.03762v7.pdf", ExternalId("arxiv", "1706.03762")),
        ("https://arxiv.org/html/2310.06825v1/#S2", ExternalId("arxiv", "2310.06825")),
        ("http://export.arxiv.org/abs/hep-th/9901001", ExternalId("arxiv", "hep-th/9901001")),
        ("https://pubmed.ncbi.nlm.nih.gov/26017442/", ExternalId("pmid", "26017442")),
        ("https://www.ncbi.nlm.nih.gov/pubmed/26017442", ExternalId("pmid", "26017442")),
        ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4481139/", ExternalId("pmcid", "PMC4481139")),
        ("https://pmc.ncbi.nlm.nih.gov/articles/PMC4481139", ExternalId("pmcid", "PMC4481139")),
        ("https://europepmc.org/article/MED/26017442", ExternalId("pmid", "26017442")),
        ("https://europepmc.org/abstract/MED/26017442", ExternalId("pmid", "26017442")),
        ("https://europepmc.org/article/PMC/PMC4481139", ExternalId("pmcid", "PMC4481139")),
        ("https://europepmc.org/article/PPR/PPR123456", None),  # preprint IDs have no scheme
        ("https://dl.acm.org/citation.cfm?id=3295222", None),  # no DOI in the URL: stays a plain web result
        ("https://arxiv.org/list/cs.CL/recent", None),
        ("https://doi.org/", None),
        ("ftp://doi.org/10.1038/nature14539", None),
        ("https://evil.example/doi.org/10.1038/nature14539", None),
        ("http://[::1", None),  # urlsplit raises: never escapes
        ("not a url", None),
    ],
)
def test_from_url(url, expected):
    assert ids.from_url(url) == expected
