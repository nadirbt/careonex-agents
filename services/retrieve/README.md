# services/retrieve — cited passages for the voice agent

Fifth container. Wraps Bedrock Knowledge Bases `Retrieve` with the metadata filters the agent
needs (`program`, `year`, `jurisdiction`, `source_id`) and logs latency on every call, which is
the number that decides whether S3 Vectors stays (see kb-sync README).

Two entry points:

```bash
# Long-running HTTP API (what the voice container calls)
docker compose up retrieve
curl -s localhost:8080/health
curl -s localhost:8080/retrieve -H 'content-type: application/json' \
  -d '{"query":"Does Medicaid pay for someone to come to the house?","program":"MLTSS","year":2026}'

# One-shot query; --save writes data/retrieval-example.json (Milestone 2 "one complete retrieval flow")
docker compose run --rm retrieve query "What is the JACC income limit for a single person?" --program JACC --save
```

The knowledge base id comes from `CAREONEX_KB_ID` or, by default, from
`config/knowledge-base.json` that kb-sync wrote to the bucket. Responses carry, per passage, the
chunk text, score, `source_url`, `title`, `program`, `effective_date`, `heading_path` and S3 key,
so the agent can say which document it is quoting.

## Experimental program-aware query expansion

See [`../../QUERY_EXPANSION_HANDOFF.md`](../../QUERY_EXPANSION_HANDOFF.md) for complete
PowerShell tests, benchmark grading and limitations. Baseline remains the default.
`careonex-retrieve query ... --search-mode expanded` opts in for one query;
`CAREONEX_RETRIEVE_SEARCH_MODE=expanded` opts in for a server process.
`careonex-retrieve compare ... --output evaluation/medicaid-ab.json` exports the
same-KB baseline/expanded comparison for blinded grading.

## Batch evaluation

To evaluate a reproducible set of fictional caller questions without changing the KB or live voice agent, see `../../BATCH_RAG_EVALUATION_HANDOFF.md`. Commands: `careonex-retrieve batch-compare` for A/B collection, then `python -m careonex_retrieve.batch_eval` after human relevance grading. Original single-question `compare` remains supported.


## General-purpose intent-aware query expansion

Set `CAREONEX_RETRIEVE_SEARCH_MODE=intent` for an opt-in local server trial or pass `--search-mode intent` for a single CLI query. This uses Bedrock Converse (default text model `amazon.nova-lite-v1:0`, override with `CAREONEX_QUERY_MODEL_ID`). See [`../../INTENT_QUERY_EXPANSION_HANDOFF.md`](../../INTENT_QUERY_EXPANSION_HANDOFF.md) for PowerShell setup, test instructions, model access requirements, A/B collection with `--candidate-mode intent`, and safe rollback. The existing single-query baseline remains the default and the historic `expanded` mode is retained.

## Live voice integration (model-free feedback)

The Nova Sonic tool now submits `search_mode=feedback` explicitly by default,
while this API's own default remains `baseline`. The feedback planner performs
its first `Retrieve` using the exact original question, may issue up to two
heading-based supplemental searches, then combines ranking using RRF. No
Bedrock text-model call is needed. Voice rollback: set
`CAREONEX_VOICE_SEARCH_MODE=baseline` on the voice process. Details:
[`../../VOICE_QUERY_EXPANSION_HANDOFF.md`](../../VOICE_QUERY_EXPANSION_HANDOFF.md).
