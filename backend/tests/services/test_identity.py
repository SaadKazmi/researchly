from researchly.connectors.base import Author, ExternalId, PaperRecord
from researchly.services.identity import dedupe, merge, normalize_title, same_paper, trigram_similarity


def rec(source, title, *, ids=(), year=2017, author="Ashish Vaswani", **kw):
    return PaperRecord(source=source, ids=list(ids), title=title, year=year, authors=[Author(author)], **kw)


def test_normalize_title_folds_case_accents_and_punctuation():
    assert normalize_title("Attention Is All You Need!") == "attention is all you need"
    assert normalize_title("Réseaux  de neurones—profonds") == "reseaux de neurones profonds"


def test_trigram_similarity_matches_pg_trgm():
    assert trigram_similarity("word", "word") == 1.0
    assert round(trigram_similarity("word", "two words"), 6) == 0.363636  # SELECT similarity('word', 'two words')
    assert trigram_similarity("", "x") == 0.0


def test_shared_identifier_means_same_paper():
    a = rec("arxiv", "Attention", ids=[ExternalId("doi", "10.48550/arxiv.1706.03762")])
    b = rec("openalex", "Something else", ids=[ExternalId("doi", "10.48550/arxiv.1706.03762")], year=1990)
    assert same_paper(a, b)


def test_fuzzy_match_needs_title_year_and_first_author():
    a = rec("arxiv", "Attention Is All You Need")
    assert same_paper(a, rec("crossref", "Attention is all you need.", year=2018))
    assert not same_paper(a, rec("crossref", "Attention is all you need", year=2019))
    assert not same_paper(a, rec("crossref", "Attention is all you need", author="Noam Shazeer"))
    assert not same_paper(a, rec("crossref", "Attention is not all you need"))


def test_surname_handles_comma_form():
    a = rec("pubmed", "Deep learning", author="LeCun, Yann", year=2015)
    b = rec("crossref", "Deep learning", author="Yann LeCun", year=2015)
    assert same_paper(a, b)


def test_merge_takes_fields_by_source_priority_and_records_provenance():
    arxiv = rec("arxiv", "Attention Is All You Need", ids=[ExternalId("arxiv", "1706.03762")], abstract="arXiv text")
    openalex = rec(
        "openalex", "Attention is All you Need", ids=[ExternalId("openalex", "W2626778328")], cited_by=120404
    )
    merged = merge([arxiv, openalex])
    assert merged.title == "Attention is All you Need"  # openalex outranks arxiv
    assert merged.abstract == "arXiv text"  # only arxiv had one
    assert merged.cited_by == 120404
    assert merged.field_sources["title"] == "openalex" and merged.field_sources["abstract"] == "arxiv"
    assert {i.scheme for i in merged.ids} == {"arxiv", "openalex"}


def test_dedupe_clusters_transitively():
    a = rec("arxiv", "Attention Is All You Need", ids=[ExternalId("arxiv", "1706.03762")])
    b = rec("hf", "Attention is all you need", ids=[ExternalId("arxiv", "1706.03762")])
    c = rec("crossref", "Deep learning", author="LeCun, Yann", year=2015)
    assert len(dedupe([a, b, c])) == 2


def test_non_latin_titles_do_not_collapse_to_digits():
    assert normalize_title("Квантовая механика 2020") == "квантовая механика 2020"
    assert normalize_title("Réseaux") == "reseaux"
    a = rec("crossref", "Квантовая механика 2020", author="Иван Петров")
    b = rec("crossref", "Тепловые потоки 2020", author="Иван Петров")
    assert not same_paper(a, b)
    assert same_paper(a, rec("openalex", "Квантовая механика 2020", author="Иван Петров"))


def test_fuzzy_needs_two_letter_tokens():
    assert not same_paper(rec("a", "2020"), rec("b", "2020"))
    assert not same_paper(rec("a", "Intro"), rec("b", "Intro"))
    assert not same_paper(rec("a", ""), rec("b", ""))


def test_surname_comma_form_takes_last_family_token():
    a = rec("a", "Visualizing data using t-SNE", author="van der Maaten, Laurens")
    b = rec("b", "Visualizing data using t-SNE", author="Laurens van der Maaten")
    assert same_paper(a, b)


def test_merge_keeps_falsy_values():
    merged = merge([rec("openalex", "T", cited_by=0, is_oa=False), rec("crossref", "T", cited_by=5, is_oa=True)])
    assert merged.cited_by == 0 and merged.is_oa is False
