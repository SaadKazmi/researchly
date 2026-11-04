from researchly.ingest.arxiv_html import parse_arxiv_html
from researchly.ingest.document import FullText, Section

PAGE = b"""<!DOCTYPE html><html><head><title>Attention</title></head><body>
<nav>chrome</nav>
<article class="ltx_document">
  <h1 class="ltx_title ltx_title_document">Attention Is All You Need</h1>
  <div class="ltx_authors">Ashish Vaswani</div>
  <div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">Abstract</h6>
    <p class="ltx_p">The dominant sequence transduction models.</p></div>
  <section class="ltx_section" id="S1"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
    <div class="ltx_para"><p class="ltx_p">Recurrent networks<span class="ltx_note ltx_role_footnote">footnote text</span> are slow
      <cite class="ltx_cite">[<a class="ltx_ref" href="#bib.bib1">1</a>]</cite>.</p></div>
    <section class="ltx_subsection" id="S1.SS1"><h3 class="ltx_title ltx_title_subsection">1.1 Attention</h3>
      <div class="ltx_para"><p class="ltx_p">We use <math alttext="QK^{T}" class="ltx_Math"><mi>Q</mi></math> scores.</p></div>
      <table class="ltx_equation ltx_eqn_table"><tr class="ltx_eqn_row"><td>
        <math alttext="\\mathrm{softmax}(x)" class="ltx_Math" display="block"><mi>s</mi></math></td></tr></table>
      <figure class="ltx_figure"><img class="ltx_graphics" src="x1.png"/>
        <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 1: </span>The Transformer.</figcaption></figure>
    </section>
  </section>
  <section class="ltx_bibliography" id="bib"><h2 class="ltx_title">References</h2>
    <ul class="ltx_biblist"><li class="ltx_bibitem" id="bib.bib1">Bahdanau et al.</li></ul></section>
</article></body></html>"""  # noqa: E501


def test_sections_carry_their_path_and_clean_text():
    full = parse_arxiv_html(PAGE)
    assert isinstance(full, FullText) and full.source == "arxiv_html" and full.license is None
    assert full.sections[0] == Section(("Abstract",), "The dominant sequence transduction models.")
    intro, sub = full.sections[1], full.sections[2]
    assert intro.path == ("1 Introduction",)
    assert intro.text == "Recurrent networks are slow [1]."  # footnote dropped, citation kept as text
    assert sub.path == ("1 Introduction", "1.1 Attention")
    assert sub.text == "We use $QK^{T}$ scores.\n\n$\\mathrm{softmax}(x)$\n\nFigure 1: The Transformer."


def test_bibliography_authors_and_chrome_are_not_sections():
    full = parse_arxiv_html(PAGE)
    assert full is not None
    texts = " ".join(s.text for s in full.sections)
    assert "Bahdanau" not in texts and "Vaswani" not in texts and "chrome" not in texts


def test_pages_without_an_article_or_text_give_none():
    assert parse_arxiv_html(b"<html><body><p>404 not found</p></body></html>") is None
    assert parse_arxiv_html(b"<html><body><article class='ltx_document'></article></body></html>") is None
    assert parse_arxiv_html(b"") is None
    assert parse_arxiv_html(b"\x00\xff garbage") is None
