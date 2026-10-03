# services/data — buckets and knowledge-corpus ingestion

The first container in the pipeline. It does two things:

1. **Ensures the S3 buckets exist** (idempotent). Missing buckets are created with versioning,
   full public-access block, default encryption and project tags. Existing buckets are left exactly
   as they are and simply become accessible to the rest of the pipeline.
2. **Ingests the approved source catalog** (`catalog/ragfile_list.csv`, mirrored from the team's
   Drive folder "RAG Knowledge Base") into the knowledge bucket, one object per source plus a
   `.metadata.json` sidecar in the format Amazon Bedrock Knowledge Bases uses for metadata filtering.
   Every run writes a snapshot manifest, which is the dataset version id.

## Buckets

| Key | Default name | Encryption | Purpose |
| --- | --- | --- | --- |
| `knowledge` | `ac215-program-kb-<account-id>` | SSE-S3 | Public program documents + sidecars. Source for the Knowledge Base. No PII. |

Override the name with `CAREONEX_KB_BUCKET` when a bucket already exists under another name.

There is deliberately no bucket for client-supplied documents. If the product ever stores PHI, it
gets its own KMS-encrypted bucket in a separate account and is never a Knowledge Base data source.

Object layout in the knowledge bucket:

```
raw/<source_id>/<file_name>                  the document (object metadata carries sha256, source_url, fetch_date)
raw/<source_id>/<file_name>.metadata.json    {"metadataAttributes": {program, year, jurisdiction, county, effective_date, ...}}
snapshots/<snapshot_id>/manifest.json        which objects (and versions) make up this dataset snapshot
```

Later pipeline steps add, in the same bucket and never touching `raw/`:

```
text/<source_id>/<file_name>.md              clean Markdown (services/extract)
chunks/<source_id>/<file_name>.jsonl         section-aware chunks (services/chunk); the Knowledge Base data source
```

## Run it

From the repo root, after `aws sso login --profile careonex-team` on the host:

```bash
make build            # docker compose build
make whoami           # prints the identity the container is using
make run              # ensure-buckets, then ingest  (== docker compose up data ingest)
make status           # bucket existence + settings
docker compose run --rm data ls raw/nj_doas/
docker compose run --rm data ingest --dry-run        # fetch + hash, upload nothing
docker compose run --rm data ingest --only nj_dmahs  # one publisher
```

Without Docker (development):

```bash
cd services/data
uv sync
uv run careonex-data status
uv run pytest -q
```

## Permissions

Everyone uses the single `AC215` permission set. It is scoped by resource prefix: course resources
are named `ac215-*`, production resources are named `careonex-*`, and the policy never mentions
`careonex-*`. The full policy and the one-time service role live in `TEAM_SETUP.md` (section
"The AC215 inline policy"). If this container exits 5 with `AccessDenied`, the permission set has
not been reprovisioned yet; no re-login is needed once it has.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | ingest finished but at least one source failed (see manifest) |
| 2 | knowledge bucket not available; run `ensure-buckets` |
| 3 | no AWS credentials (SSO login expired or ~/.aws not mounted) |
| 4 | bucket name exists but belongs to someone else |
| 5 | AccessDenied: current role lacks S3 permissions |
