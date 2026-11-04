from researchly.ingest.chunking import CHUNK_TOKENS, Piece, chunk_text


def words(text: str) -> int:
    return len(text.split())


def test_short_text_is_one_chunk():
    assert chunk_text("Attention is all you need.", words) == [Piece("Attention is all you need.", 5)]


def test_blank_text_has_no_chunks():
    assert chunk_text("  \n\n ", words) == []


def test_long_text_is_split_within_the_budget_with_overlap():
    sentences = [f"Sentence number {i} talks about attention heads." for i in range(400)]  # 7 words each
    text = " ".join(sentences)
    pieces = chunk_text(text, words)
    assert len(pieces) > 4
    assert all(p.tokens == words(p.text) <= CHUNK_TOKENS for p in pieces)
    assert all(p.text in text for p in pieces)  # chunks are verbatim spans
    first_tail = pieces[0].text.split()[-5:]
    assert " ".join(first_tail) in pieces[1].text  # consecutive chunks overlap
