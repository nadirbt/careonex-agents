# services/extract — raw documents to clean Markdown

Second container in the pipeline. Reads every document under `raw/` in the knowledge bucket and
writes `text/<source_id>/<file_name>.md` beside a copy of the sidecar enriched with
`derived_from`, `source_sha256`, `text_sha256`, `extractor` and `extractor_version`.

Why a separate text form: raw HTML from nj.gov and va.gov changes bytes on every request (nonces,
timestamps, menus), so raw hashes cannot say whether the *content* moved. The HTML converter keeps
the page's main region and drops scripts, navigation, footer and forms (content-detection libraries
were tried first and silently dropped nj.gov eligibility paragraphs), so the text hash becomes a
meaningful change signal; the run summary lists every
document whose text actually changed. PDFs go through pymupdf4llm, which keeps headings and renders
tables as Markdown tables so an eligibility row stays with its program name.

```bash
docker compose run --rm extract run                 # everything not yet up to date
docker compose run --rm extract run --only nj_doas  # one publisher
docker compose run --rm extract run --force         # after changing the extractor
docker compose run --rm extract run --dry-run
uv run careonex-extract convert some.pdf | less     # inspect extractor quality locally
```

Idempotent: a text object produced from the same raw bytes by the same `EXTRACTOR_VERSION` is
skipped. Bump `EXTRACTOR_VERSION` in `convert.py` when the conversion rules change so everything is
re-done once. Summary: `snapshots/<id>/extract.json` in the bucket and `data/<id>/extract.json` locally.
