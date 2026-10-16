# Known issue: 2025 Medicaid figures in the 2026 DoAS side-by-side table

Status: open, not fixed. Recorded 2026-10-16 while porting Marco's DoAS table extractor
(`services/extract/careonex_extract/doas_tables.py`). Check it right after the next knowledge-base rebuild.

## The source

The state's "DoAS Programs Side-by-Side (2026)" PDF (created 2026-03-26, SHA-256 `109cf593…b25b`, the
same bytes as our catalog row) labels each eligibility row with the year its figures apply to. The state
updated some columns for 2026 and left others at 2025:

| Column (page 2 unless noted) | Figures | Label printed in the PDF |
| --- | --- | --- |
| MLTSS/PACE: SSI, Medicaid Only (institutional), NJ Care Special Medicaid | $998.25, **$2,901**/mo, $1,305/mo … | **(2025)** |
| JACC | $4,855 / $6,582, resources $40,000 / $60,000 | (2026) |
| Statewide Respite (SRCP) | $2,982 / $5,964 | (2026) |
| Alzheimer's Adult Day Services (AADSP) | $50,256 / $58,632 | **(2025)** |
| Page 3: PAAD, Senior Gold, MSPs, Lifeline | various | (2026) |

The MLTSS Medicaid income limit is 300% of the federal SSI payment: 2025 is $967 × 3 = $2,901, 2026 is
$994 × 3 = $2,982. The PDF's own SRCP column already uses $2,982, and so does our curated file
(`services/data/catalog/curated/nj_doas_2026_program_limits.md`, from the NJ FamilyCare MLTSS Application
Guidance, April 2026). So the data is the state's, correctly extracted; the MLTSS and AADSP columns were
simply not updated. Same pattern as the 2026 Program Guide quoting the 2025 JACC limit.

The extractor reproduces the PDF faithfully: all 56 dollar figures match the PDF text, each under the right
program (checked offline on 2026-10-16).

## The risk

The retrieve service decides which year a passage applies to with `figure_year()`
(`services/retrieve/careonex_retrieve/retriever.py`): the **latest year named anywhere in the passage
text**. The chunker prepends the heading path to every chunk, and the extractor puts every program
section under `# NJ Division of Aging Services Programs Side-by-Side (2026)` and `## Page 2 (March 2026)`.

So the MLTSS chunk that says "$2,901 … (2025)" most likely gets year **2026**. `prefer_latest()` then keeps
it next to the curated 2026 passage (both have program "All DoAS programs") instead of withholding it,
and the agent may quote $2,901 as the current MLTSS income limit. The AADSP figures have the same problem.

## How to check (after the rebuild)

1. `POST /retrieve {"query": "what is the MLTSS income limit"}` and `... "Alzheimer's adult day income limit"`.
2. If a side-by-side passage with "(2025)" figures appears in `passages` (not under `superseded`), the
   issue is confirmed.
3. Ask the voice agent the same question (`just smoke` or the mic client). It must say $2,982 per month.

## Possible fixes (not implemented)

- **Year from the row label.** Have the extractor, or `figure_year()`, take the year from the
  "(2025)"/"(2026)" label printed on each eligibility row instead of the page heading. The 2025 rows are then
  withheld by the existing `prefer_latest` rule whenever a 2026 passage exists.
- **Ignore heading years in `figure_year()`.** Count only years in the chunk body, not in the prepended
  heading path. Simpler, but changes year tagging for every document, so re-run the retrieval eval.
- **Interim:** if the check fails before a fix lands, the curated file still has the correct 2026 figures;
  the side-by-side PDF could be dropped from the catalog until the fix is in.
