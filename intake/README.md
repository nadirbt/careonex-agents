# Intake schema (Section 2b, "Intake schema and consent language")

This is the state the dialog agent builds up during a call and hands off at
the end (SOW Section 3, stage 6 "Wrap-up": *"A post-call model writes the
intake JSON, disposition and summary; a human reviews a sample"*).

## Two different artifacts — don't conflate them

- `form_schema.v1.json` — the internal phone-intake form, captured
section by section from the team's actual tool. This describes what a
human intake worker records *during/after* a call. It has no consent
field because consent isn't decided on this call — it was already given
on the public web form.
- **The public careonex.com web form** — captures the initial request and
the "you agree to be contacted" consent language (SOW Section 2b, row 1).
This is upstream of everything here. The scheduler (SOW stage 0,
"Eligibility to call") checks *that* record before dialing; the agent
never asks for consent to be contacted, because by definition the family
already gave it before the call was placed. What the agent's Opening
script (stage 1) does is disclose recording and ask "is now a good time,"
which is a courtesy re-confirmation, not the original consent capture.

If that's wrong — if the internal form is expected to also record consent,
or if there's a third consent artifact I haven't seen — flag it, because the
`IntakeConsent` state below is currently modeled as separate from
`IntakeRecord` specifically on that assumption.

## Why a confidence-per-field model

SOW stage 3 ("Dialog agent") requires the agent to hold "a needs profile
with a confidence per field," and stage 6 requires flagging "disagreement
between live state and post-call extraction." A plain dict of field values
can't represent either of those. `state.py` gives every field:

- a `value`
- a `confidence`: `unasked` / `stated` / `confirmed`
- `source`: which turn (or tool) it came from, for debugging and eval

This also anticipates the field-accuracy metric (SOW: "Field and
disposition accuracy vs. gold ... ≥ 90%"): the eval harness can score only
`confirmed` fields separately from `stated` ones, since those have
different reliability.

## Files

- `form_schema.v1.json` — the raw form structure as captured, versioned so
future edits to the form don't silently change what the agent extracts.
- `state.py` — a typed `IntakeState` built from the schema: one dataclass
field per leaf field in the form, each wrapped as a `TrackedField`, plus
helper methods (`set`, `confirm`, `missing_required`, `to_json`) the
dialog-agent loop and the post-call extraction step both use.
- `consent.py` — the separate, small `CallConsent` model: whether the number
being dialed has recorded consent, whether the family said "yes, now is a
good time" at the top **of *this* call, and whether an opt-out was r**ecorded.
This is what SOW stage 0 ("Eligibility to call") and the success metric
"Calls to numbers without recorded consent: 0" actually gate on — kept
separate from `IntakeState` so a consent bug can't hide inside intake
field logic, and vice versa.



## What's NOT modeled yet

- Tool schemas for Nova Sonic to *write* into this state mid-call (a
`record_field(field, value)` style tool). `state.py` is the data model
those tools would target; the tool specs themselves are a follow-up once
this schema is agreed on.
- The disposition taxonomy (SOW mentions "disposition" as a tracked/graded
field throughout, but the captured form JSON doesn't enumerate disposition
values). `IntakeState.disposition` is typed as a free string for now —
flag if there's a fixed disposition list to encode instead.

