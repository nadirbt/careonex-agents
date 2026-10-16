# CareOneX Evaluation

**Evaluation code and its required fixtures are retained in this cleaned repository.** Historical generated replay outputs were removed only when no test or script depends on them.

## Key files

| File | Purpose |
| --- | --- |
| `services/voice/nova_sonic/text_eval.py` | Scores voice transcripts against a reference answer (word cosine, WER, expected/forbidden phrases, lookup evidence) |
| `services/voice/tests/test_text_eval.py` | Text-evaluation unit tests |
| `evaluation/medicaid_voice_gold.json` | Voice reference answer and phrase checklist |
| `evaluation/chunking_retrieval_benchmark.py` | A/B/C chunking retrieval evaluation and scoring |
| `evaluation/intent_parent_experiment.py` | Baseline vs model-free feedback and parent-context benchmark |
| `evaluation/feedback_parent_experiment.py` | Feedback benchmark CLI wrapper |
| `evaluation/batch_questions.json` | 25 benchmark questions |
| `evaluation/universal_homecare_questions.json` | 40 general home-care questions |
| `evaluation/chunk_abc_prior_graded.json` | Existing manually/provisionally graded comparison fixture |
| `evaluation/universal_homecare_v2_full_prior.json` | Previous 40-question baseline fixture **required by retrieval regression tests** |
| `evaluation/universal_homecare_pilot_previous.json` | Previous pilot fixture **required by regression tests** |
| `evaluation/graded_examples/`, `evaluation/prior_graded/` | Historical grading examples |
| `evaluation/fixtures/source_documents/` | Previously top-level `chunk_abc_input/` source snapshots for experimental reproducibility |

## Text evaluation — no AWS call

Run from the repository root, after making a **new** voice smoke-test report (do not commit reports containing personal information):

```powershell
uv run --python 3.12 --directory services/voice python -m nova_sonic.text_eval --report "$PWD\voice-smoke-v5\voice-smoke.json" --gold "$PWD\evaluation\medicaid_voice_gold.json" --output "$PWD\voice-text-evaluation.json"
```

Change `--report` to the path where your smoke-test JSON was saved. The text evaluator does **not** establish factual correctness. Human checking is required.

## Offline tests — no AWS call

```powershell
uv run --python 3.12 --directory services/voice pytest -q
uv run --python 3.12 --directory services/retrieve pytest -q
```

## Retrieval experiments — AWS costs/permissions apply

To collect evidence for feedback vs baseline using the previously created staging Knowledge Base:

```powershell
$env:AWS_PROFILE = "careonex-team"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:CAREONEX_KB_ID = "<team-approved-staging-KB-ID>"
$env:CAREONEX_KB_BUCKET = "<team-approved-staging-bucket>"
uv run --python 3.12 --directory services/retrieve python "$PWD\evaluation\feedback_parent_experiment.py" collect --planner feedback --questions "$PWD\evaluation\universal_homecare_questions.json" --kb $env:CAREONEX_KB_ID --bucket $env:CAREONEX_KB_BUCKET --limit 5 --output "$PWD\evaluation\new_feedback_pilot.json"
```

Use the correct staging KB and bucket for your AWS environment. `feedback` mode does not call Nova Lite. Do not commit fresh question transcripts or passages containing sensitive information. Review/grade relevance before making Precision, MRR, or NDCG claims.

**Note:** the experimental `intent` planner invokes a text model and may be denied by your SSO policy. There is no need to enable it for the voice agent.

## Before activating a live Knowledge Base

**No production KB selection, re-ingestion, or deployment is done by this repository update.**
Ask the team which existing KB is approved for each environment. In a retrieval-only
session, explicitly set `CAREONEX_KB_ID` to the approved KB and check the returned
`knowledge_base_id` for a test retrieval before using it in the voice agent.
Do not run `just run` or `kb-sync` just to test feedback; those can rebuild data.

## Automated blind grading of the saved 40-question feedback run (Bedrock; no human review)

Run this from the repository root after AWS SSO login. This automatically
judges each distinct question/passage pair (218 for the saved 40-question
fixture), without revealing the baseline/feedback arm, rank, or score to the
LLM. Identical passages within a question are graded once and given the same
score for both arms. It computes Precision@5, MRR@5 and pooled NDCG@5 and
writes both a JSON result and a Markdown comparison report. **No manual
review step is required.**

```powershell
$env:AWS_PROFILE = "careonex-team"
$env:AWS_DEFAULT_REGION = "us-east-1"
aws sso login --profile careonex-team
uv run --python 3.12 --directory services/retrieve python "$PWD\evaluation\auto_feedback_grading.py" --input "$PWD\evaluation\universal_homecare_v2_full_prior.json" --output "$PWD\evaluation\reviews\feedback_40_auto.json" --report "$PWD\evaluation\reviews\feedback_40_auto.md" --model-id amazon.nova-lite-v1:0 --confirm-model-calls
```

The chosen Bedrock model needs `bedrock:InvokeModel` permission and incurs AWS
charges (218 small model calls for this historical fixture). The script
**checkpoints every grade**: if Bedrock throttles or permissions fail, fix the
cause and rerun the same command to resume without grading completed items.
It fails closed on invalid model output and rejects checkpoints from a different
source snapshot, model, or rubric. It never rebuilds or writes to the KB. The
`evaluation/reviews/` folder is Git-ignored because its results may contain
sensitive or licensed text.

**Interpretation:** These are automated *LLM-judged* relevance estimates, not
human-validated ground truth or proof of factual correctness. A grade >=1
counts as relevant for Precision@5/MRR@5. The NDCG ideal is drawn only from
the union of retrieved passages, not all possible documents. Results refer to
the **saved historical retrieval run**, not newer retrieval/merge code; rerun
`collect` with the current code to measure current retrieval quality.

The prior offline `blind_feedback_grading.py export/score` workflow remains
available for backward compatibility; it is **not needed** for this automated
path. The script does not silently replace missing LLM grades with heuristics.

## Official 2026 NJ DoAS table acceptance test (offline)

The extractor can now handle split program headings and entirely empty table
spacer columns. To verify **the actual government PDF** before ingestion,
download its four-page March 2026 version locally and run:

```powershell
$env:CAREONEX_DOAS_PDF_PATH = "C:\path\to\DoAS Programs Side-by-Side (2026).pdf"
uv run --python 3.12 --directory services/extract pytest -q tests/test_doas_tables.py
```

The official-document test skips if `CAREONEX_DOAS_PDF_PATH` is absent. A passing
synthetic test by itself does **not** establish fidelity to the real PDF.
