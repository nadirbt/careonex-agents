# CareOneX — A/B/C chunking experiment (staging only)

This update adds a **third, hierarchical chunking option** and a reproducible experiment using the actual original algorithm.

| Variant | Implementation | Child size | Indexed content |
|---|---|---|---|
| A `legacy` | Original `CHUNKER_VERSION=2` from `careonex-agents-feat-sonic_with_rag.zip` | Original defaults: target 1,600 chars; max requested 2,800 chars | Original chunks; can mix short sections |
| B `section` | Section-safe `CHUNKER_VERSION=4` (table-row safety fix) | Target 1,600 chars, max 2,800 chars, up to 120-char overlap | Section-safe chunks |
| C `hierarchical` | **New** `hierarchical.py` | Parent max 2,400 chars; indexed children target 950 chars, up to 80-char overlap | Searchable child passages with full heading and topic context; separate **non-indexed** parent documents linked by ID |

The hierarchical approach uses Markdown structure and deterministic generic topical cues (e.g., Eligibility, Services, Application). It is *not* embedding-driven semantic chunking. When no heading or topical cue exists, it uses the original paragraph/sentence splitting with a maximum parent size. Tables retain their headers and remain in separate topical parents. The number and length of chunks are inspectable in `manifest.json`.

**Important:** Parent text does not magically appear in Nova Sonic's responses. The optional benchmark flag `--include-parent-context` loads parent texts for inspection alongside the corresponding retrieved children, using the parent ID and staged S3 URI. The production retrieval/voice path remains unchanged. Using parent context as part of generation would require a separately evaluated retrieval-service change.

## Wide program comparison table fix (October 2026)

The initial 20-document export showed the 2026 NJ DoAS side-by-side comparison
table failing under `section` and `hierarchical` because a single table row
was larger than the target chunk size. This release safely converts **oversized
rows** into smaller records containing **both the row label and the program
column name**. It preserves each cell's text (splitting long prose only on
sentence/word boundaries) instead of cutting a financial figure away from its
label. A malformed row, missing labels or an unbreakable oversized value
continues to fail closed for manual review. The original `legacy` strategy is
left unchanged, including its occasional oversized output.

If you've already fetched the 20 original Markdown documents into
`chunk_abc_input`, **do not re-download them**. Keep that input folder, extract
this updated code into your project folder, and use a new, empty output folder:

```powershell
$runId = "chunk-abc-" + (Get-Date -Format "yyyyMMdd-HHmmss")
uv run --python 3.12 --directory services/chunk python -m careonex_chunk.experiment export --input-dir "$PWD\chunk_abc_input" --output-dir "$PWD\chunk_abc_export_fixed" --experiment-id $runId
$manifest = Get-Content "$PWD\chunk_abc_export_fixed\manifest.json" -Raw | ConvertFrom-Json
$manifest.errors
$manifest.results | Group-Object strategy | Select-Object Name,Count
```

You should see **no errors** and **20 results per strategy** (60 in total).
If an error remains, send the failed source Markdown file for inspection
before uploading anything to staging. Do **not** run `stage-upload` until then.
A test with a synthetic seven-column comparison table and the focused
regression suite succeeded locally; this source document itself has not yet
been tested in the remote AWS account.

## Safety and scope

- By default, **everything runs locally**. The live Knowledge Base and `chunks/` prefix are not changed.
- For the AWS experiment, uploads require `--confirm-upload` and target only `experiments/chunking/<run>/<variant>/chunks/` and `/parents/`. Stage upload refuses any reused prefix and never deletes live objects.
- `careonex-kb sync` now allows experimental `CAREONEX_CHUNKS_PREFIX` and `CAREONEX_KB_CONFIG_KEY`, with checks preventing a staging ingestion from using the live KB name, index name or config key.
- You must explicitly create three **distinct** staging KBs and three separate S3 Vector indexes before running the AWS retrieval comparison. This may incur AWS storage, indexing, retrieval and data-processing costs. Verify available IAM permissions.

## Step 1 — Extract ZIP and sign into AWS (Windows PowerShell)

Open PowerShell in the extracted project root (`careonex-agents-rag-evaluation`).

```powershell
$env:AWS_PROFILE = "careonex-team"
$env:AWS_DEFAULT_REGION = "us-east-1"
aws sso login --profile careonex-team
$account = (aws sts get-caller-identity --profile careonex-team | ConvertFrom-Json).Account
$bucket = "ac215-program-kb-$account"
$runId = "chunk-abc-20261009"   # Pick a NEW ID every time you re-run/upload.
```

## Step 2 — Read-only export of the existing extracted documents

This command downloads `text/*.md` and their sidecars from your existing bucket. It does not write to S3.

```powershell
uv run --python 3.12 --directory services/chunk python -m careonex_chunk.experiment fetch-text --bucket $bucket --output-dir "$PWD\chunk_abc_input"
```

If your extracted Markdown is already available locally, you may instead point `--input-dir` to that directory. It must contain all documents you want in every variant, not just the sample curated document bundled with the project.

## Step 3 — Offline A/B/C export (no AWS writes)

```powershell
uv run --python 3.12 --directory services/chunk python -m careonex_chunk.experiment export --input-dir "$PWD\chunk_abc_input" --output-dir "$PWD\chunk_abc_export" --experiment-id $runId
```

Inspect `chunk_abc_export/manifest.json` for all documents and verify there are no errors. The children and parent text files live under `strategies/<variant>/chunks/` and `/parents/`.

You can test the code without AWS:

```powershell
uv run --python 3.12 --directory services/chunk pytest -q
```

## Step 4 — Stage the chunks into separate, unused S3 prefixes (AWS writes)

**Only do this if you are comfortable creating staging resources in the shared AWS account.**

```powershell
uv run --python 3.12 --directory services/chunk python -m careonex_chunk.experiment stage-upload --export-dir "$PWD\chunk_abc_export" --bucket $bucket --confirm-upload
```

No live objects under `chunks/` are modified or removed.

## Step 5 — Provision and ingest three isolated staging KBs

Run the following **only if AWS permissions allow provisioning three new staging KBs and indexes**. It does **not** switch your live retriever to the staging KBs.

```powershell
$env:CAREONEX_KB_BUCKET = $bucket
foreach ($variant in @("legacy", "section", "hierarchical")) {
    $env:CAREONEX_CHUNKS_PREFIX = "experiments/chunking/$runId/$variant/chunks"
    $env:CAREONEX_KB_CONFIG_KEY = "config/staging/$runId/$variant.json"
    $env:CAREONEX_KB_NAME = "ac215-$runId-$variant"
    $env:CAREONEX_VECTOR_INDEX = "$runId-$variant"
    uv run --python 3.12 --directory services/kb-sync careonex-kb sync --data-dir "$PWD\chunk_abc_kbs\$variant"
    if ($LASTEXITCODE -ne 0) { throw "Staging KB sync failed for $variant" }
}
```

The three created ID files are:

- `chunk_abc_kbs/legacy/knowledge-base.json`
- `chunk_abc_kbs/section/knowledge-base.json`
- `chunk_abc_kbs/hierarchical/knowledge-base.json`

Keep these staging settings out of the terminal you use for your live Nova Sonic server. Before running normal `careonex-kb sync`, unset `CAREONEX_CHUNKS_PREFIX`, `CAREONEX_KB_CONFIG_KEY`, `CAREONEX_KB_NAME` and `CAREONEX_VECTOR_INDEX` or open a clean PowerShell window.

## Step 6 — Run all 25 benchmark questions against every staging KB

```powershell
$a = (Get-Content "$PWD\chunk_abc_kbs\legacy\knowledge-base.json" -Raw | ConvertFrom-Json).knowledge_base_id
$b = (Get-Content "$PWD\chunk_abc_kbs\section\knowledge-base.json" -Raw | ConvertFrom-Json).knowledge_base_id
$c = (Get-Content "$PWD\chunk_abc_kbs\hierarchical\knowledge-base.json" -Raw | ConvertFrom-Json).knowledge_base_id
uv run --python 3.12 --directory services/retrieve python "$PWD\evaluation\chunking_retrieval_benchmark.py" collect --questions "$PWD\evaluation\batch_questions.json" --legacy-kb $a --section-kb $b --hierarchical-kb $c --top-k 5 --include-parent-context --output "$PWD\chunk_abc_retrieval.json"
```

This uses **the exact same original question and top-k setting** for each KB. It intentionally does not use query expansion, reranking, or program filters so that chunking is the principal variable. The `parent_text` field (when available) is supplementary context; retrieval ranking uses only indexed children. The optional parent lookup adds S3 latency, so compare Bedrock retrieval timings separately from end-to-end timings.

## Step 7 — Independently judge retrieved passages and score

In `chunk_abc_retrieval.json`, set `relevance` on every retrieved passage:

- `0`: Does not help answer the question.
- `1`: Partially relevant but insufficient alone.
- `2`: Directly answer-bearing.

Do not change the caller queries or passage text. Ideally, have teammates grade without seeing the strategy labels. The scorer refuses missing grades and conflicting grades for identical passage texts.

```powershell
uv run --python 3.12 --directory services/retrieve python "$PWD\evaluation\chunking_retrieval_benchmark.py" score --input "$PWD\chunk_abc_retrieval.json" --output "$PWD\chunk_abc_metrics.json"
```

The scorer reports per-question and average **Precision@k, MRR@k, NDCG@k and observed retrieval latency**. The NDCG ideal ranking is based on unique candidate passages retrieved across all three indexes, not all relevant passages in the full KB; therefore it is not an exhaustive recall measure. Compare evidence completeness, correct program attribution and citations during manual grading. Do not claim final voice-answer improvements without an end-to-end Nova Sonic test.

## What has been verified here

- The exact original v2 algorithm was imported from the original supplied ZIP as `legacy_chunker.py`.
- An offline export of the bundled sample Markdown completed for all three strategies.
- 13 focused unit tests passed for the new and existing chunker behavior, staging export, parent links, and the scoring program.
- **AWS staging KB creation, ingestion, retrieval accuracy, and voice-answer gains were NOT run or measured in this environment.**

These changes are intentionally additive: ordinary `careonex-chunk run` still uses the previous section-safe strategy, and default retrieval/voice behavior is unchanged.
