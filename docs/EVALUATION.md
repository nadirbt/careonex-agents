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
uv run --python 3.12 --directory services/retrieve python "$PWD\evaluation\feedback_parent_experiment.py" collect --planner feedback --questions "$PWD\evaluation\universal_homecare_questions.json" --kb UYC7EK0ZDV --bucket ac215-program-kb-117949645823 --limit 5 --output "$PWD\evaluation\new_feedback_pilot.json"
```

Use the correct staging KB and bucket for your AWS environment. `feedback` mode does not call Nova Lite. Do not commit fresh question transcripts or passages containing sensitive information. Review/grade relevance before making Precision, MRR, or NDCG claims.

**Note:** the experimental `intent` planner invokes a text model and may be denied by your SSO policy. There is no need to enable it for the voice agent.
