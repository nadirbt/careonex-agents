# Knowledge sources for the RAG (Section 2a)

This is the corpus the dialog agent draws on when a family asks how care is paid
for, who qualifies, how to apply, what care includes, or how to judge a
provider — the "Answering (RAG)" stage in the milestone SOW.

## Why cards, not raw pages

The SOW's 2a sources ("a few hundred pages" of HTML/PDF across NJ FamilyCare,
NJ Division of Aging Services, Medicare/VA, and NJ Consumer Affairs) are
written for caseworkers and applicants reading at their own pace, not for a
sentence spoken aloud in a phone call. Each **program card** in
`program_cards/` distills one program down to what a family actually asks:
who qualifies, what it pays for, how to apply, and a two-sentence spoken
answer with a source name attached.

This also settles the Q1 question of whether Nova 2 Sonic needs a vector
database. The full 2a corpus is only 8–10 programs. Condensed to cards, that
is roughly 6–10k tokens total — small enough to hand to the model directly,
either in the system prompt or behind a simple lookup tool (`get_program_card
(program)` — no embeddings, no cosine similarity, no re-ranker). Vector RAG
is worth adding only if the corpus grows past what fits in a prompt, or if
recall against a much larger set of documents becomes the bottleneck. Until
then, this smaller design is easier to audit, which matters for the
"claims traceable to a retrieved chunk ≥ 95%" metric.

## Card schema

Each file in `program_cards/` is Markdown with a small frontmatter block:

```
---
id: nj-mltss
program: NJ FamilyCare Medicaid MLTSS
category: medicaid
publisher: NJ Dept. of Human Services, Division of Medical Assistance and Health Services (DMAHS)
source_url: https://www.nj.gov/humanservices/dmahs/individuals-families/familycare/mltss/
effective_date: 2026-01-01
fetch_date: 2026-09-28
review_status: draft-needs-verification
---
```

- **source_url, publisher** — so an answer can name its source, per the SOW.
- **effective_date** — the date the cited figures (income limits, dollar
  caps) are believed current as of. Where a source did not clearly date its
  numbers, this is our best estimate and the card says so.
- **fetch_date** — when this card was last checked against the live page.
  Compare fetch_date to today to find a stale card before it costs a family
  a wrong answer.
- **review_status** — `draft-needs-verification` until a human confirms the
  figures against the source; `verified` once someone has. No card should be
  used on a live call while still `draft-needs-verification`.

The body follows a fixed section order so every program answers the same
questions in the same shape:

1. **Summary** — one sentence, family framing.
2. **Who qualifies**
3. **What it pays for**
4. **How to apply**
5. **Key numbers** — dollar figures and dates, so staleness is visible.
6. **What to say on the call** — the actual 1–2 sentence spoken answer,
   because the SOW requires answers to be checked for length and reading
   level (stage 4, "Answering (RAG)").
7. **Sources**

## What's covered

| Card | Programs |
| --- | --- |
| `nj-mltss.md` | NJ FamilyCare / Medicaid Managed Long Term Services and Supports |
| `nj-jacc.md` | Jersey Assistance for Community Caregiving (JACC) |
| `nj-statewide-respite.md` | NJ Statewide Respite Care Program |
| `nj-alzheimers-adult-day.md` | NJ Alzheimer's Adult Day Services Program |
| `nj-pace.md` | Program of All-Inclusive Care for the Elderly (PACE), NJ |
| `medicare-home-health.md` | Medicare home health benefit |
| `va-home-care-benefits.md` | VA Aid & Attendance / Housebound pension, Homemaker & Home Health Aide |
| `nj-hcsf-consumer-guide.md` | NJ Health Care Service Firm rules — how to tell a legitimate agency from an unlicensed one |
| `cms-care-compare-ratings.md` | What a Care Compare star rating means and does not mean |

## What's deliberately *not* a card

The county-by-county ADRC directory and the statewide licensed home health
agency list (`data.nj.gov`, ~2,000 rows) are lookup tables, not prose a family
would want read aloud. They belong behind a tool call
(`get_county_office(county)`, `search_licensed_agencies(county, type)`),
not stuffed into the grounding text. A partial, explicitly incomplete start
is in `directories/nj-adrc-county-offices.json` — see the file for what is
still missing before it is production-ready.

## Re-crawl and staleness policy (per SOW 2a)

- Re-crawl each source on a schedule (weekly is reasonable given "few hundred
  pages" total) and diff against the stored card.
- If two sources conflict, the one with the later `effective_date` wins.
- A page that does not parse cleanly is excluded rather than guessed at —
  "I will have someone follow up on that" beats a half-read rule spoken
  aloud.
- Nothing with `review_status: draft-needs-verification` should be used to
  answer a live call.

## Loading cards in code

`nova_sonic/knowledge.py` parses every card in `program_cards/` and can
render them into a single grounding block (for a system prompt) or return
them individually (for a lookup tool). See `tests/test_knowledge.py` for the
schema contract every card must satisfy.
