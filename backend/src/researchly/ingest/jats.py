"""Europe PMC JATS full text → sections (research 2026-11-04-step2b-fulltext §3)."""

from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from researchly.ingest.document import FullText, Section, clean

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_BLOCKS = ("p", "disp-formula", "list", "def-list")


def _text(el: Element | None) -> str:
    return clean("".join(el.itertext())) if el is not None else ""


def _own_blocks(sec: Element) -> list[str]:
    out = []
    for child in sec:
        if child.tag in _BLOCKS:
            out.append(_text(child))
        elif child.tag in ("fig", "table-wrap"):
            out.append(_text(child.find("caption")))
    return [b for b in out if b]


def _walk(sec: Element, parent: tuple[str, ...], out: list[Section]) -> None:
    title = _text(sec.find("title"))
    path = (*parent, title) if title else parent
    if blocks := _own_blocks(sec):
        out.append(Section(path or ("Body",), "\n\n".join(blocks)))
    for child in sec.findall("sec"):
        _walk(child, path, out)


def parse_jats(body: bytes) -> FullText | None:
    try:
        root = fromstring(body)
    except (DefusedXmlException, ParseError, ValueError):  # entity bombs, DTDs, HTML error pages, truncation
        return None
    if root.tag != "article":
        return None
    sections: list[Section] = []
    if abstract := _text(root.find("front/article-meta/abstract")):
        sections.append(Section(("Abstract",), abstract))
    content = root.find("body")
    if content is not None:
        if lead := _own_blocks(content):
            sections.append(Section(("Body",), "\n\n".join(lead)))
        for sec in content.findall("sec"):
            _walk(sec, (), sections)
    if len(sections) <= (1 if abstract else 0):
        return None  # an abstract alone is not full text
    lic = root.find("front/article-meta/permissions/license")
    license_ = (lic.get(_XLINK_HREF) or _text(lic) or None) if lic is not None else None
    return FullText("jats", sections, license_)
