# services/kb-sync — vector index, knowledge base, ingestion

Fourth container. Everything is get-or-create, so it runs on every pipeline invocation:

1. S3 Vectors bucket `ac215-program-vectors-<account-id>` (SSE-S3) and index `program-kb`
   (1024 dims, cosine, float32) sized for Titan Text Embeddings v2.
2. Bedrock Knowledge Base `ac215-program-kb` using the service role `AC215-KnowledgeBaseRole`,
   storage = that index.
3. Data source `chunks` over `s3://ac215-program-kb-<account-id>/chunks/` with chunking strategy
   `NONE`: each pre-chunked object becomes one vector, its sidecar supplies filterable metadata.
4. Starts an ingestion job and polls it to completion; prints the statistics.

The resulting ids are written to `config/knowledge-base.json` in the knowledge bucket and to
`data/knowledge-base.json`, which is how the `retrieve` service finds the knowledge base.

```bash
docker compose run --rm kb-sync sync
docker compose run --rm kb-sync sync --skip-ingest   # provision only
docker compose run --rm kb-sync sync --no-wait       # kick off ingestion, do not poll
docker compose run --rm kb-sync status
```

Exit codes: 1 ingestion did not complete; 3 no credentials; 5 AccessDenied (permission set);
6 the service role is missing or Bedrock cannot assume it (owner creates it, see TEAM_SETUP.md).

Stores considered: S3 Vectors (chosen: cents per month, ~100-300 ms queries), OpenSearch
Serverless (tens of ms but ~$175/month minimum), Aurora pgvector (~$45/month minimum). Re-evaluate
if p95 retrieval latency threatens the one-second voice turn target; the raw/text/chunks data is
untouched by that choice.
