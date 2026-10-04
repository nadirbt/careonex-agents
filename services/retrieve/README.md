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
