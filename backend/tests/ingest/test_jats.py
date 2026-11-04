from researchly.ingest.document import Section
from researchly.ingest.jats import parse_jats

ARTICLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="research-article">
<front><article-meta>
  <article-id pub-id-type="pmcid">PMC5334499</article-id>
  <permissions><license license-type="cc-by" xlink:href="https://creativecommons.org/licenses/by/4.0/">
    <license-p>Open access.</license-p></license></permissions>
  <abstract><p>Deep learning <italic>works</italic>.</p></abstract>
</article-meta></front>
<body>
  <p>Lead paragraph.</p>
  <sec><title>Introduction</title><p>First.</p>
    <fig id="f1"><label>Figure 1</label><caption><p>A network.</p></caption></fig>
    <sec><title>Background</title><p>Second.</p></sec>
  </sec>
  <sec><p>Untitled section text.</p></sec>
</body>
<back><ref-list><ref id="r1"><mixed-citation>LeCun 2015</mixed-citation></ref></ref-list></back>
</article>"""


def test_sections_paths_and_licence():
    full = parse_jats(ARTICLE)
    assert full is not None and full.source == "jats"
    assert full.license == "https://creativecommons.org/licenses/by/4.0/"
    assert full.sections == [
        Section(("Abstract",), "Deep learning works."),
        Section(("Body",), "Lead paragraph."),
        Section(("Introduction",), "First.\n\nA network."),
        Section(("Introduction", "Background"), "Second."),
        Section(("Body",), "Untitled section text."),
    ]


def test_licence_text_when_there_is_no_link():
    body = ARTICLE.replace(b' xlink:href="https://creativecommons.org/licenses/by/4.0/"', b"")
    full = parse_jats(body)
    assert full is not None and full.license == "Open access."


def test_references_are_not_sections():
    full = parse_jats(ARTICLE)
    assert full is not None and all("LeCun" not in s.text for s in full.sections)


def test_unreadable_or_foreign_xml_gives_none():
    assert parse_jats(b"<html><body>error</body></html>") is None
    assert parse_jats(b"<article><front/></article>") is None
    assert parse_jats(b"not xml") is None
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xx">]><article>&x;</article>'
    assert parse_jats(bomb) is None
