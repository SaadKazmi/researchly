# ADR 0009: Full-text tiers, chunking and hybrid retrieval on one Postgres

- Status: accepted
- Date: 2026-11-06
- Spec sections: §8.2, §9.1, §9.2, §9.3, §16, §19.3

## Context
Step 2b turns a paper record into verified, searchable full text. It needs a fetch order, a politeness policy for crawled pages, a chunker, vector storage and hybrid search. All of it runs keyless, in the one Postgres. Research is in `docs/research/2026-11-04-step2b-fulltext.md` (R-fulltext) and `docs/research/2026-11-04-step2b-search.md` (R-search).

## Decision
- **Tiers:** arXiv HTML, then Europe PMC JATS for open-access papers with a PMCID, then the PDF through Docling. The LaTeX-source tier is dropped: arxiv.org's robots.txt has `Disallow: /e-print` and `/src`, and the LaTeXML HTML already covers every paper probed (R-fulltext §1, §2).
- **PoliteClient (`connectors/http.py`):**
  - **Content-type gate:** each call passes an `accept` set and a `max_bytes`, checked on the headers before the body is read. The PDF tier accepts `application/pdf` and `application/octet-stream`, caps at 50 MB, and then requires `%PDF-` in the first bytes.
  - **Per-hop pacing:** each redirect hop waits for its own host's limit and holds one host permit at a time.
  - **Crawl-delay:** `arxiv.org` is 15 s with concurrency 1 in `limits.yaml`; `Robots` feeds any other host's `Crawl-delay` to the limiter.
  - **robots.txt (`connectors/robots.py`, Protego 0.7.0, RFC 9309):** a 4xx allows everything, a 5xx or network error disallows and is retried after 10 minutes, the cache lasts 24 h, and a `Crawl-delay` over 60 s skips the host. Only crawled pages are checked: arXiv HTML and PDF hosts. The Europe PMC REST API is not.
- **Chunking:** semchunk at `chunk_size` 512 with 15% overlap, per section. The token counter is the Qwen3-Embedding `tokenizer.json`, so chunks are measured in the embedder's tokens. semchunk keeps chunk plus overlap within the size, so the mean content is smaller than 512.
- **Schema (migration 0003):**
  - `paper_section` and `chunk`, plus `paper.fulltext_source` and `paper.fulltext_at`.
  - `paper_embedding` and `chunk_embedding`: `halfvec(1024)`, primary key `(paper_id | chunk_id, model_id)`.
  - HNSW with `halfvec_cosine_ops` (m 16, ef_construction 64).
  - Queries set `hnsw.iterative_scan = relaxed_order` and `hnsw.ef_search = 100`.
- **BM25:** the engine comes from `capability.bm25_engine`.
  - `pg_textsearch`: `USING bm25` indexes, `text_config = 'english'`.
  - `tsvector`: a generated `bm25_tsv` column with a GIN index, the `'english'` config.
  - The BM25 objects (`bm25_tsv`, `ix_paper_bm25`, `ix_chunk_bm25`) are created only in the migration, never in the models, and `test_models_match_migrations` skips them.
  - Query text is OR-ed: `plainto_tsquery`'s lexemes joined with `|` and cast back with `::tsquery` (not `to_tsquery`, which would stem them a second time: `diseas` becomes `disea`), because `websearch_to_tsquery` ANDs every word and returns nothing for questions (R-search §3).
  - The `backend-bm25` CI job builds `infra/postgres` and runs `-m db` with `RESEARCHLY_EXPECT_BM25=pg_textsearch`. The default `backend` job covers tsvector.
- **Fusion:** RRF (k = 60, 100 candidates per list) of the lexical and dense lists in one SQL statement. The rerank is a separate step after it.
- **SPECTER2:** deferred to step 5. It needs an ONNX export that we host, and where to host it is the user's decision (R-search §1).

## Consequences
- **One database:** no vector or search service; the Docker image carries pg_textsearch, native mode falls back to tsvector. Ranking differs slightly between engines (BM25 vs `ts_rank`), so tests assert the fusion contract, not exact scores.
- **Drift risk:** the pg_textsearch index expression must match the query's. `retrieval.py` repeats `PAPER_DOC` from the migration, and the query tests on both engines catch drift.
- **robots.txt gap:** it is checked for the first PDF host only, not for redirect hops (PoliteClient follows redirects internally).
- **Ingest holds a transaction:** `ingest_paper` fetches inside the caller's open transaction. The step 3 job wrapper should split read, fetch and write.
- **Scoped lexical search on pg_textsearch** filters after the index's top 100, so a narrow scope over a large library can starve the lexical list. The dense side uses iterative scan.
- **Chunk size:** the research measured about 350 Qwen3 tokens per chunk on real papers. `tests/platform/test_chunking_tokens.py` measured a mean of 392 and a max of 420 on synthetic text. Both fit the embedder's 512-token window (fastembed truncates there); a full 512-token chunk plus its section-path prefix loses a few tail tokens to that truncation.
- BM25 is English only.

## Evidence
- **R-fulltext:** 15 of 16 arXiv HTML probes returned 200 (the 16th was a made-up ID); the Europe PMC and PDF probes set the content-type allowlist and the `%PDF-` sniff; robots.txt and Protego facts.
- **R-search:** the OR-ed query, `relaxed_order` and `ef_search` behaviour, and the SPECTER2 hosting question.
- **Tests at 87a13c0:** backend 512 passed, 2 skipped; `-m db` 45 passed on tsvector (pgvector pg18) and 45 passed on pg_textsearch (the `infra/postgres` image).
- **CI run 42223460180** (756eafa): every job green, including `backend-bm25`.
