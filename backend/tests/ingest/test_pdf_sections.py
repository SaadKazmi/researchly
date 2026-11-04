from researchly.ingest.document import Section
from researchly.ingest.pdf import markdown_sections

MARKDOWN = """Attention Is All You Need

## Abstract

The dominant models.

## 1 Introduction

Recurrent networks.

<!-- image -->

Second paragraph.

## References

[1] Bahdanau.

## A Appendix

Extra.
"""


def test_headings_become_flat_sections_without_references_or_image_markers():
    assert markdown_sections(MARKDOWN) == [
        Section(("Body",), "Attention Is All You Need"),
        Section(("Abstract",), "The dominant models."),
        Section(("1 Introduction",), "Recurrent networks.\n\nSecond paragraph."),
        Section(("A Appendix",), "Extra."),
    ]


def test_numbered_reference_headings_are_dropped_too():
    assert markdown_sections("## 7 References\n\n[1] x\n\n## Bibliography\n\ny\n") == []


def test_empty_markdown_has_no_sections():
    assert markdown_sections("") == [] and markdown_sections("<!-- image -->\n") == []


def test_headings_inside_code_fences_stay_in_the_body():
    md = "## Method\n\n```python\n# comment\nx = 1\n```\n\nAfter.\n"
    # paragraphs are whitespace-collapsed, so the fence reads as one line; what matters is no "comment" section
    assert markdown_sections(md) == [Section(("Method",), "```python # comment x = 1 ```\n\nAfter.")]
