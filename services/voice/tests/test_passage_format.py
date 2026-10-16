import re

import nova_sonic.tools as t
from nova_sonic.tools import PASSAGE_MAX_CHARS, normalize_county, speakable_passage


def test_markdown_formatting_removed_wording_and_numbers_kept():
    raw = (
        "## **How to Apply**\n\n"
        "- Who: age **21 or older**, needs nursing-home level of care.\n"
        "- Income limit 2026: $2,982 per month for a single applicant.<br>Income above that requires a QIT.\n"
        "1. Call [member services](https://example.org/ms) on the plan card.\n"
    )
    out = speakable_passage(raw)
    assert not re.search(r"\*\*|^#|^- |<br|\]\(", out, re.M)
    assert "How to Apply" in out and "age 21 or older, needs nursing-home level of care." in out
    assert "$2,982 per month for a single applicant. Income above that requires a QIT." in out
    assert "1) Call member services on the plan card." in out and "https://" not in out


def test_table_cells_labelled_only_when_every_column_has_a_header():
    full = "|Program|Age|Income limit 2026|\n|---|---|---|\n|JACC|60+|$4,855/mo|\n|MLTSS|21+|$2,982/mo|"
    assert speakable_passage(full).splitlines() == ["JACC; Age: 60+; Income limit 2026: $4,855/mo.", "MLTSS; Age: 21+; Income limit 2026: $2,982/mo."]

    # A merged group-title row is not per-column headers: keep it as its own line, label nothing.
    grouped = "|**2026**|**MEDICAID WAIVER**||**NON-MEDICAID**|\n|---|---|---|---|\n|**Cost Share**|NO *|YES – Sliding Scale|YES|"
    lines = speakable_passage(grouped).splitlines()
    assert lines == ["2026; MEDICAID WAIVER; NON-MEDICAID.", "Cost Share; NO *; YES – Sliding Scale; YES."]


def test_footnote_marks_survive():
    out = speakable_passage("|Cost share|NO *|\n\n* Cost share may apply in assisted living.\n\n** Service package remains the same.")
    assert "NO *" in out and "* Cost share may apply in assisted living." in out and "** Service package remains the same." in out


def test_long_passage_cut_at_sentence_with_marker():
    text = " ".join(f"Rule {i} applies to this program." for i in range(200))
    out = speakable_passage(text)
    assert len(out) < len(text) and out.endswith("[passage continues; not all of it is shown]")
    assert out.split(" [passage continues")[0].endswith(".")
    assert len(speakable_passage("Short rule.")) == len("Short rule.")
    assert PASSAGE_MAX_CHARS >= 2800  # never below the chunker cap, so normal chunks arrive whole


def test_normalize_county():
    assert normalize_county("Monmouth") == "Monmouth"
    assert normalize_county("monmouth county") == "Monmouth"
    assert normalize_county("Cape May County, NJ") == "Cape May"
    assert normalize_county("Bergen County, New Jersey") == "Bergen"
    for bad in ("New Jersey", "NJ", "Newark", "", None, "Kings County", 7):
        assert normalize_county(bad) is None


def test_state_name_is_not_used_as_a_county(monkeypatch):
    calls = []
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    monkeypatch.setattr(t, "_post_json", lambda url, payload, timeout: calls.append(payload) or {"passages": [{"text": "x", "s3_key": "k"}]})

    out = t.lookup_program_info_sync({"query": "Does Medicaid pay for home care?", "county": "New Jersey", "program": "Medicaid"})
    assert calls[-1]["query"] == "Does Medicaid pay for home care?"  # no "(New Jersey County)" suffix, nothing invented
    assert "'New Jersey' is not a New Jersey county" in out["guidance"]

    out = t.lookup_program_info_sync({"query": "Does Medicaid pay for home care?", "county": "monmouth county"})
    assert calls[-1]["query"] == "Does Medicaid pay for home care? (Monmouth County)"
    assert "not a New Jersey county" not in out["guidance"]
