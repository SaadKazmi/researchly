"""arXiv's LaTeXML HTML (arxiv.org/html/<id>) → sections (research 2026-11-04-step2b-fulltext §1)."""

import lxml.etree as etree
import lxml.html

from researchly.ingest.document import FullText, Section, clean

_SECTIONS = {"ltx_section", "ltx_subsection", "ltx_subsubsection", "ltx_paragraph", "ltx_appendix"}
_DROP = {"ltx_note", "ltx_authors", "ltx_bibliography", "ltx_page_footer"}  # footnotes, bylines, references


def _classes(el: etree._Element) -> set[str]:
    return set((el.get("class") or "").split())


def _is_section(el: etree._Element) -> bool:
    return el.tag == "section" and bool(_classes(el) & _SECTIONS)


def _title(section: etree._Element) -> str:
    for child in section:
        if child.tag in ("h2", "h3", "h4", "h5", "h6") and "ltx_title" in _classes(child):
            return clean(child.text_content())
    return ""


def _blocks(container: etree._Element, owner: etree._Element | None) -> list[str]:
    """Paragraphs, display equations and captions whose nearest section is `owner`."""
    out = []
    for el in container.iter("p", "figcaption", "table"):
        if el.tag == "table" and not _classes(el) & {"ltx_equation", "ltx_equationgroup"}:
            continue
        if el.tag == "p" and next(el.iterancestors("figcaption"), None) is not None:
            continue  # a caption's own <p> is read with the caption
        if next((a for a in el.iterancestors() if _is_section(a)), None) is not owner:
            continue
        if text := clean(el.text_content()):
            out.append(text)
    return out


def parse_arxiv_html(body: bytes) -> FullText | None:
    try:
        root = lxml.html.document_fromstring(body)
    except (etree.ParserError, ValueError):
        return None
    article = next(root.iter("article"), None)
    if article is None:
        return None
    for el in list(article.iter()):
        if isinstance(el.tag, str) and _classes(el) & _DROP and el.getparent() is not None:
            el.drop_tree()  # keeps the tail text
    for math in list(article.iter("math")):
        span = etree.Element("span")
        span.text, span.tail = f"${math.get('alttext', '')}$", math.tail
        math.getparent().replace(math, span)  # pyright: ignore[reportOptionalMemberAccess]
    sections: list[Section] = []
    abstract = next((d for d in article.iter("div") if "ltx_abstract" in _classes(d)), None)
    if abstract is not None and (paras := [clean(p.text_content()) for p in abstract.iter("p")]):
        sections.append(Section(("Abstract",), "\n\n".join(p for p in paras if p)))
    for sec in (el for el in article.iter("section") if _is_section(el)):
        path = tuple(_title(a) for a in reversed(list(sec.iterancestors())) if _is_section(a)) + (_title(sec),)
        if blocks := _blocks(sec, sec):
            sections.append(Section(tuple(p for p in path if p) or ("Body",), "\n\n".join(blocks)))
    return FullText("arxiv_html", sections) if sections else None
