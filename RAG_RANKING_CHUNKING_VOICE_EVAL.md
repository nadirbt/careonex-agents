# CareOneX RAG improvements: ranking, chunking, text evaluation

This is an experimental **copy** of the October 9 voice/RAG branch. It does **not** write to the team's S3 bucket, update the KB, or change any deployed service by itself.

## 1. Similarity and relevance ranking

Bedrock Knowledge Bases already performs vector retrieval. Its `score` is a provider relevance score; do **not** assume it equals embedding cosine similarity. This update supports three second-stage modes:

- `none` (default): preserve current Bedrock ranking and latency baseline.
- `lexical`: rerank candidate passages by a blend of **local TF-IDF cosine** and original Bedrock rank. No model calls. This is *lexical* cosine, not semantic embedding similarity.
- `titan`: rerank by cosine between **Titan Text Embeddings V2** for the query and each candidate. Additional Bedrock calls; can be **slow/costly** and requires `bedrock:InvokeModel` permissions and model access. Use for experiments before enabling it during live voice calls.

Switch mode in the **retrieval service's** PowerShell window before starting it:

```powershell
$env:CAREONEX_RETRIEVE_RERANK = "lexical"    # or "none" for baseline
uv run --python 3.12 --directory services/retrieve careonex-retrieve serve
```

Run *the same* questions for baseline and lexical on separate server runs. Test answer-bearing retrieved passages rather than optimizing cosine scores alone. An optional dense embedding experiment uses `$env:CAREONEX_RETRIEVE_RERANK="titan"`, but can add one AWS embedding request **per candidate plus one for the query**. Do not use it in latency-sensitive calls without benchmarking. The implementation leaves score metadata visible as `rerank_score`.

**A/B ranking evaluation with manually graded candidate passages:** use `evaluation/ranking_example.json` as the schema. For each real query, first save the **10 candidates returned by the baseline Bedrock query**; have a teammate grade each passage: 0 irrelevant, 1 partially relevant, 2 sufficient evidence. Save a JSON array of those cases, then run:

```powershell
uv run --python 3.12 --directory services/retrieve python -m careonex_retrieve.ranking_eval --input "$PWD\evaluation\ranking_example.json" --top-k 3 --output "$PWD\evaluation\ranking-results.json"
```

This prints **Precision@k, Recall@k within the candidate pool, MRR@k, NDCG@k**, before/after lexical reranking. The supplied example is *synthetic*; it is NOT evidence of improved real-world performance. Titan mode needs separate live A/B measurement.

AWS offers native rerank models too; evaluate those if the team's IAM and KB configuration allow it. This update does not activate an AWS-managed reranker automatically.

## 2. Better segmentation / chunking

`services/chunk/careonex_chunk/chunker.py` now:

- Never merges a short JACC section into MLTSS, PCA, or any different heading. This prevents cross-program eligibility contamination.
- Prepends the full heading path to every passage so figures keep their program identity.
- Splits long paragraphs at sentence and then word boundaries, including unpunctuated text.
- Keeps table rows with column headers, repeating the header when a table needs to be split. **Fails closed** on a single over-wide table row rather than silently losing its header/figures.
- Uses up to 120 characters of preceding *prose from the same heading only*, never overlaps tables across sections.

Config: `CAREONEX_CHUNK_TARGET_CHARS` default 1600; `CAREONEX_CHUNK_MAX_CHARS` default 2800; `CAREONEX_CHUNK_OVERLAP_CHARS` default 120. `CHUNKER_VERSION` is bumped to 3. **Do not run `careonex-chunk run` or KB sync on the team's shared bucket without approval**: version changes can rewrite and delete existing chunk objects. Local preview, no AWS modifications:

```powershell
uv run --python 3.12 --directory services/chunk careonex-chunk preview "$PWD\services\data\catalog\curated\nj_doas_2026_program_limits.md"
```

For a meaningful live chunking A/B comparison: build a separate staging knowledge base with the same documents and embedding model, run the same labelled retrieval cases in both indexes, and compare quality and latency. The local structural tests confirm boundaries, **not** a quality lift in the real index.

## 3. Text-to-text evaluation of voice-to-voice answers

Audio-only voice conversations can be evaluated for *content* by comparing **user transcript -> assistant transcript** against a human-reviewed gold question/answer and, optionally, a text-only baseline answer. This evaluates *content loss, retrieval usage, numerical/claim-review prompts*, but it **cannot** replace independent speech evaluation (WER against hand transcripts), timing/barge-in, speech quality, or human assessment of factual grounding.

### From an existing prerecorded voice smoke test

Run your usual voice test, which saves `voice-smoke-output/voice-smoke.json`. Then:

```powershell
uv run --python 3.12 --directory services/voice python -m nova_sonic.text_eval --report "$PWD\voice-smoke-output\voice-smoke.json" --gold "$PWD\evaluation\medicaid_voice_gold.json" --output "$PWD\evaluation\voice-text-results.json"
```

It reports input **WER versus the scripted recording text**, assistant answer word-cosine versus gold, optional answer word-cosine versus a text-only baseline, keyword-presence checks, whether RAG was called and returned sent passages, and maximum lexical overlap with retrieved evidence. **Similarity is not correctness**: a confidently wrong eligibility figure may score high. Review the assistant's claims against approved NJ sources and flag unsupported claims manually.

### From a live mic session (synthetic test data only)

```powershell
uv run --python 3.12 --extra mic --directory services/voice careonex-voice --eval-report "$PWD\evaluation\my-synthetic-call.json"
uv run --python 3.12 --directory services/voice python -m nova_sonic.text_eval --report "$PWD\evaluation\my-synthetic-call.json" --gold "$PWD\evaluation\medicaid_voice_gold.json"
```

Only use fictional names and phone numbers, because this explicitly saves a transcript and tool outputs. **Do not store, commit, or share real callers' identifiable information.** Without `--eval-report`, microphone conversations remain unsaved by this new code.

The supplied `evaluation/medicaid_voice_gold.json` is a **starter**, not a reviewed legal reference. Before using it for grading program correctness, a team member should validate each claim against up-to-date official sources. For real evaluations use at least 20–30 diverse questions (PCA vs MLTSS, JACC, Medicare, fallback/unsupported, ambiguous county, interruption, multiple languages) with gold answers and reviewer-confirmed passages.

## 4. Checks and deployment

Offline tests (run separately in Windows):

```powershell
uv run --python 3.12 --directory services/chunk pytest -q
uv run --python 3.12 --directory services/retrieve pytest -q
uv run --python 3.12 --directory services/voice pytest -q
```

The three areas should be promoted only if measured retrieval relevance, evidence-grounded answer accuracy, and voice latency improve or remain acceptable. Changes to chunking take effect **only after explicit, approved re-chunking and re-ingestion in staging/production**.
