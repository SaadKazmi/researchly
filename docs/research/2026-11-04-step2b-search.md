# Step 2b research: SPECTER2, BM25, pgvector, RRF

Date 2026-11-04. All claims below were run live (Windows 10, uv scratch envs with torch for references only; Docker image built from `infra/postgres`: PG 18.6, pgvector 0.8.7, pg_textsearch 1.5.1).

## Recommendations

1. **SPECTER2 = base + proximity adapter (`allenai/specter2`), not `specter2_base` alone.** The model card says base is "to be used along with the adapters"; avg SciRepEval 69.1 (base) vs 71.1 (adapters), MDCR (38.0, 32.4) vs (38.4, 33.0). Base vs adapter embeddings differ (cosine 0.96 on the same text), so mixing is not safe. Spec §9.2 should say "specter2_base + proximity adapter". (§1a)
2. **Do not depend on the one public ONNX export** (`adamlabadorf/specter2-proximity-onnx`: single author, 0 downloads, cosine 0.994 vs torch, not the 4e-6 it claims). (§1b)
3. **Export once ourselves** (torch + `adapters` in a throwaway env, opset 17, output `last_hidden_state`, fp32, 441 MB), host it as a release asset or HF repo we own (Apache-2.0), load with `TextEmbedding.add_custom_model(pooling=CLS, normalization=True)`. Verified cosine 1.0000 vs torch through fastembed. First-run export without torch is not possible. (§1c, §1d)
4. **Input** is `title + "[SEP]" + abstract` (literal `[SEP]`, no spaces), max 512 tokens, 768 dims, L2-normalised. (§1d)
5. **Native Postgres has no pg_textsearch**: pg0's PG 18.1 ships `vector` and `pg_trgm` only. `0001_baseline` already records `capability.bm25_engine = 'tsvector'` there. (§2)
6. **BM25 (Docker):** expression index `bm25((title || ' ' || coalesce(abstract,''))) WITH (text_config='english')`, score with `<@> to_bm25query(q, 'index')` (negative; lower is better; non-matches score 0, so filter `< 0`). (§3)
7. **BM25 fallback (native):** stored generated `tsvector` ('english') + GIN, OR-ed `plainto_tsquery`, `ts_rank_cd(tsv, q, 32)`. Not `websearch_to_tsquery` for natural-language queries (it ANDs: 0 hits). (§3)
8. **Dense:** `halfvec(1024)` + `hnsw (vec halfvec_cosine_ops)`, `SET LOCAL hnsw.iterative_scan = relaxed_order`, `hnsw.ef_search` 40-100; re-sort with a MATERIALIZED CTE and `ORDER BY d + 0`. (§4)
9. **Python binding:** add `pgvector==0.5.1` (MIT) to `backend/pyproject.toml`; it is NOT there today, and the CLI lock needs `uv lock --directory cli` too. (§4)
10. **RRF k=60 in one SQL statement** over both top-N lists; cross-encoder rerank runs in Python on the fused top-N afterwards. (§5)

## 1. SPECTER2 without torch

**(a)** HF cards (`allenai/specter2_base`, `allenai/specter2`): base is trained on 6M citation triplets; task adapters are trained on top. "For general embedding purposes, use allenai/specter2" (the proximity adapter). Table from the card:

| Model | SciRepEval avg | MDCR (MAP, R@5) |
|---|---|---|
| SPECTER2 Base | 69.1 | (38.0, 32.4) |
| SPECTER2 + adapters | 71.1 | (38.4, 33.0) |

Measured here on 4 papers: cosine(base, base+proximity) = 0.957-0.965, so they are different embedding spaces.

**(b)** HF search for `specter2` onnx: only `adamlabadorf/specter2-proximity-onnx` (Apache-2.0, created 2026-02-15, 0 downloads, adapter fused, external-data `.onnx.data`). Run against my torch reference with identical token ids: cosine 0.9937-0.9976 (README claims max abs diff 4e-6), and 0.9937 vs my own export. Not trustworthy. `Claudiusvs/specter2-proximity-merged` has no licence and no ONNX. fastembed has no SPECTER2 in its catalogue; `add_custom_model` works (below).

**(c)** The proximity adapter is a bottleneck adapter with a ReLU (768->48->768) in every layer, so it cannot be folded into the weights. It is traced into the graph. Our one-off export (not shipped in the product):

```
uv run --no-project --python 3.12 --with torch --with transformers --with adapters --with onnx --with onnxscript python export.py
# m = AutoAdapterModel.from_pretrained('allenai/specter2_base'); m.load_adapter("allenai/specter2", source="hf", load_as="proximity", set_active=True)
# torch.onnx.export(Wrapper(m) -> m(input_ids, attention_mask).last_hidden_state, ..., dynamic_axes b/s, opset_version=17, dynamo=False)
```
About 20 lines, ~1 minute. Output `model.onnx` 441 MB (adapter included). Output must be the full `last_hidden_state` (3-D), because fastembed's CLS pooling indexes `[:,0]`; a 2-D CLS output crashed it. `tokenizer_config.json` needs `"model_max_length": 512` or fastembed raises "Could not determine the maximum context length". Quantising to int8 was not tried (ADR 0007 saw 0.9996 on Qwen3; re-verify for SPECTER2 before shipping). Hosting is a maintenance cost: a repo/release we own, pinned by sha256, plus an ADR; exporting at first run needs torch, which is banned.

**(d)** Numeric check (4 title/abstract pairs, torch `adapters` reference = `last_hidden_state[:,0,:]`):

| Path | cosine vs torch |
|---|---|
| our ONNX via onnxruntime, padded and unpadded | 0.99999998-1.00000006 |
| our ONNX via fastembed custom model | 0.99999996-1.00000006 |
| adamlabadorf ONNX | 0.9937-0.9976 |

```python
TextEmbedding.add_custom_model(model="researchly/specter2-proximity", pooling=PoolingType.CLS, normalization=True,
    sources=ModelSource(hf="<our-repo>"), dim=768, model_file="model.onnx")
vecs = list(TextEmbedding("researchly/specter2-proximity").embed([f"{title}[SEP]{abstract or ''}"]))
```
fastembed 0.9.1 resolves the literal `[SEP]` to the special token (ids identical to HF's). Use the same `{model_id, dim}` bookkeeping as §9.2.

## 2. Native Postgres

`~/.pg0/installation/18.1.0/share/extension/` has `vector.control`, `pg_trgm.control` and the stock contrib set; no `pg_textsearch`. ADR 0002 already says so. `provision.py` only creates `pg_textsearch` when it is in `shared_preload_libraries`; `0001_baseline` then inserts `capability('bm25_engine') = "tsvector"` (or `"pg_textsearch"`). Step 2b code must branch on that capability.

## 3. Lexical search (both engines, run in the repo image)

pg_textsearch 1.5.1 (PG 17/18, needs `shared_preload_libraries`, already set in the Dockerfile CMD). Facts from its README and runs: one `text_config` per index (required), options `k1=1.2 b=0.75`; corpus statistics are index-wide (IDF is global even for a candidate subset); ranking is OR-style (any term scores). Limits: partitioned tables have per-partition statistics; RLS leaks term statistics; no `PREPARE TRANSACTION`; implicit `<@>` is not resolved inside PL/pgSQL (pass the index name); a filter plus ranking is not a single index scan; multilingual needs one partial index per language; adding a generated column rewrote the table and rebuilt the BM25 index.

```sql
CREATE INDEX p_bm25 ON paper USING bm25 ((title || ' ' || coalesce(abstract,''))) WITH (text_config='english');
-- global top-N (index scan, verified by EXPLAIN: "Index Scan using p_bm25 ... Order By")
SELECT id, (title || ' ' || coalesce(abstract,'')) <@> to_bm25query('retrieval augmented generation','p_bm25') AS s
FROM paper ORDER BY (title || ' ' || coalesce(abstract,'')) <@> to_bm25query('retrieval augmented generation','p_bm25') LIMIT 50;
-- candidate set: add WHERE id = ANY($ids); the planner uses the pkey and scores standalone (verified); non-matches score 0
```
The expression in `ORDER BY` must repeat the index expression exactly. Result on 20k rows: the real RAG paper scored -5.14 and the DPR paper -1.95; both retrieved.

**tsvector fallback** (same 20k rows):
```sql
ALTER TABLE paper ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || coalesce(abstract,''))) STORED;
CREATE INDEX paper_tsv ON paper USING gin (tsv);
SELECT id, ts_rank_cd(tsv, q, 32) r
FROM paper, to_tsquery('english', replace(plainto_tsquery('english', $1)::text, ' & ', ' | ')) q
WHERE tsv @@ q ORDER BY r DESC LIMIT 50;
```

Correction (step 2b final review): `to_tsquery` re-stems the already-stemmed lexemes, so words such as "disease" or "database" never match. Cast instead: `replace(plainto_tsquery('english', $1)::text, ' & ', ' | ')::tsquery`.
`websearch_to_tsquery('english','how does retrieval augmented generation reduce hallucination')` matched 0 rows; the OR rewrite matched 3,336. `'english'` stems (`generate` matched 3,335 rows of `generating`; `'simple'` matched 0) and keeps `crispr-cas9` and `brca1` as lexemes; use `'english'` in both engines so results are comparable. Weakness: `ts_rank_cd` has no IDF and the OR query reads every matching row, so cap candidates. Non-English papers: accept English stemming for now, partial index per language is the later path.

## 4. pgvector

Live: pgvector 0.8.7 (Docker). pg0 has 0.8.5, the ADR 0002 floor; everything below works in 0.8.x.
```sql
CREATE TABLE paper_embedding (paper_id uuid, model_id text, vec halfvec(1024) NOT NULL, PRIMARY KEY (paper_id, model_id));
CREATE INDEX ON paper_embedding USING hnsw (vec halfvec_cosine_ops) WITH (m=16, ef_construction=64);
SET LOCAL hnsw.iterative_scan = relaxed_order;   -- also: strict_order; hnsw.max_scan_tuples default 20000
SET LOCAL hnsw.ef_search = 100;                  -- default 40
```
Evidence (forced HNSW plan, filter with no index, `LIMIT 50`): `iterative_scan=off` returned **1** row; `relaxed_order` returned **50**. Relaxed results can be slightly out of order, so re-sort (`MATERIALIZED` CTE, `ORDER BY d + 0`). A filter that has its own index (pkey `= ANY(ids)`, small sets) makes the planner skip HNSW and do an exact scan, which is fine.

Binding: `pgvector` 0.5.1 on PyPI (MIT, released 2026-10-09, Python >=3.10). Not in `backend/pyproject.toml` (only a pytest marker mentions it). Verified with SQLAlchemy 2.x + psycopg 3: `from pgvector.sqlalchemy import HALFVEC`; `HALFVEC(1024)` column; numpy insert; `col.cosine_distance(v)` ordering; HNSW created. Reads came back as plain lists unless `register_vector` is used; pass numpy float32 on write.

## 5. RRF (k=60) in one statement

Run in the image with both indexes (`$1` text, `$2` halfvec, `$3` candidate ids, `$4` N); the plan showed the BM25 index scan and the HNSW or pkey scan feeding a hash full join:
```sql
WITH bm AS MATERIALIZED (
  SELECT id, row_number() OVER (ORDER BY s) AS r FROM (
    SELECT id, (title || ' ' || coalesce(abstract,'')) <@> to_bm25query($1,'p_bm25') AS s FROM paper
    WHERE id = ANY($3)
    ORDER BY (title || ' ' || coalesce(abstract,'')) <@> to_bm25query($1,'p_bm25') LIMIT $4) t WHERE s < 0),
dn AS MATERIALIZED (
  SELECT paper_id AS id, d FROM (
    SELECT paper_id, vec <=> $2 AS d FROM paper_embedding WHERE paper_id = ANY($3)
    ORDER BY vec <=> $2 LIMIT $4) t ORDER BY d + 0),
dr AS (SELECT id, row_number() OVER (ORDER BY d) AS r FROM dn)
SELECT id, coalesce(1.0/(60+bm.r),0) + coalesce(1.0/(60+dr.r),0) AS rrf, bm.r AS bm25_rank, dr.r AS dense_rank
FROM bm FULL JOIN dr USING (id) ORDER BY rrf DESC LIMIT 50;
```
Output was sensible: a paper ranked 6th lexically and 2nd densely beat a paper that was first in only one list (0.03128 vs 0.01639). For the tsvector engine, swap the `bm` CTE for the `ts_rank_cd` query (`ORDER BY r DESC`). Where `$3` is "all papers", drop the `WHERE`.

**Rerank slot:** the SQL returns the fused top 50-100 `(id, rrf)`. Python then loads title+abstract (or chunk text) and runs the bge-reranker-v2-m3 cross-encoder (`Reranker.scores`, ADR 0007) on that list; the relevance gate and §10 ranking consume the cross-encoder score. Rerank depth stays a step-4 decision (50 pairs cost 11-51 s on CPU).

Cleanup: Docker container `r2b-pgc` and image `r2b-pg` removed. Scratch scripts are in the session scratchpad `research2b-search/`; nothing in the repo besides this file was changed.
