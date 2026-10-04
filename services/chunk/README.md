# services/chunk — Markdown to section-aware chunks

Third container. Reads `text/<source_id>/<file>.md`, splits it into chunks, and writes **one S3
object per chunk** under `chunks/<source_id>/<file>/<order>-<sha>.md`, each with a sidecar that
carries the document's attributes plus `heading_path`, `chunk_order`, `chunk_count`.

One object per chunk is the layout the Bedrock Knowledge Base consumes with chunking strategy
`NONE`, so what you see in S3 is exactly what becomes a vector. Stale chunk objects from a
previous run are deleted, and `snapshots/<id>/chunks.jsonl` lists every chunk with its text for
inspection and for the Milestone 2 retrieval example.

Chunking rules (`chunker.py`): headings start sections and the full heading path is prepended to
every chunk; tables are never split (header row repeated if a table alone exceeds the cap);
long sections split at paragraph then sentence boundaries toward `CAREONEX_CHUNK_TARGET_CHARS`
(1600 ≈ 400 tokens, cap 2800); sections under 200 chars fold into their neighbour.

```bash
docker compose run --rm chunk run
docker compose run --rm chunk run --only nj_doas_program_guide --force
uv run careonex-chunk preview some.md          # see the pieces locally
```

Bump `CHUNKER_VERSION` when the rules change so every document is re-chunked once.
