from careonex_chunk.chunker import chunk_markdown, parse_sections

DOC = """# 2026 Division of Aging Services Programs

Intro paragraph about the side-by-side table.

## JACC

Jersey Assistance for Community Caregiving serves people 60 and older at nursing home level of care.

| Household | Income | Resources |
| --- | --- | --- |
| Individual | $4,855 | $40,000 |
| Couple | $6,582 | $60,000 |

Cost share applies on a sliding scale.

## Statewide Respite Care Program

Tiny.

## MLTSS

""" + ("MLTSS paragraph sentence number one about eligibility and the five-year look-back. " * 40).strip() + """

""" + ("Second long MLTSS paragraph about qualified income trusts and the county social service agency. " * 40).strip() + "\n"


def test_sections_and_tables_parse():
    secs = parse_sections(DOC)
    jacc = next(s for s in secs if s.heading_path[-1] == "JACC")
    kinds = [b.kind for b in jacc.blocks]
    assert kinds == ["para", "table", "para"]
    assert jacc.heading_path == ["2026 Division of Aging Services Programs", "JACC"]


def test_heading_path_prefixes_every_chunk_and_table_stays_whole():
    chunks = chunk_markdown(DOC, target=1600, max_chars=2800, min_chars=200)
    jacc = [c for c in chunks if c.heading_path[-1:] == ["JACC"]]
    assert len(jacc) == 1
    assert jacc[0].text.startswith("2026 Division of Aging Services Programs > JACC")
    assert "| Individual | $4,855 | $40,000 |" in jacc[0].text and "| Couple |" in jacc[0].text


def test_long_section_splits_but_program_sections_never_merge():
    chunks = chunk_markdown(DOC, target=1600, max_chars=2800, min_chars=200)
    mltss = [c for c in chunks if c.heading_path[-1:] == ["MLTSS"]]
    respite = [c for c in chunks if c.heading_path[-1:] == ["Statewide Respite Care Program"]]
    assert len(mltss) >= 2
    assert len(respite) == 1  # safety: do not mix program eligibility into MLTSS
    assert all(c.chars <= 2800 for c in chunks)
    assert all("Tiny." not in c.text for c in mltss)
    assert chunk_markdown(DOC)[0].chunk_id == chunk_markdown(DOC)[0].chunk_id


def test_no_punctuation_paragraph_is_bounded_and_lossless():
    tokens = [f"word{i}" for i in range(200)]
    chunks = chunk_markdown("# MLTSS\n\n" + " ".join(tokens), target=150, max_chars=180, min_chars=5)
    assert all(c.chars <= 180 for c in chunks)
    joined = " ".join(c.text.split("\n\n", 1)[1] for c in chunks)
    assert all(token in joined.split() for token in tokens)


def test_overlap_only_for_prose_and_within_same_heading():
    md = "# JACC\n\n" + ("Care at home is available with a review. " * 18) + "\n\n# MLTSS\n\nOther program."
    chunks = chunk_markdown(md, target=170, max_chars=170, overlap_chars=35)
    jacc = [c for c in chunks if c.heading_path == ["JACC"]]
    assert len(jacc) > 1
    assert all(len(c.text) <= 170 for c in chunks)
    assert not any("Other program." in c.text for c in jacc)


def test_unsafe_oversized_table_row_fails_closed():
    md = "# JACC\n\n| Program | Limit |\n| --- | --- |\n| JACC | " + "9"*500 + " |"
    import pytest
    with pytest.raises(ValueError, match="table row"):
        chunk_markdown(md, target=160, max_chars=200)


def test_oversized_table_repeats_header():
    rows = "\n".join(f"| County {i} | 555-010{i % 10} | Office {i} street address that is fairly long |" for i in range(80))
    md = "# ADRC contacts\n\n| County | Phone | Address |\n| --- | --- | --- |\n" + rows + "\n"
    chunks = chunk_markdown(md, target=1200, max_chars=2000, min_chars=50)
    assert len(chunks) > 1
    assert all("| County | Phone | Address |" in c.text for c in chunks)
    assert all(c.chars <= 2000 + 200 for c in chunks)


def test_wide_comparison_table_retains_program_and_row_labels():
    columns = ["Eligibility field", "MLTSS", "JACC", "PACE", "Medicare", "VA", "Respite"]
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    values = ["Asset limits"] + [f"{name} requires review of {name}_VALUE_{i}; " +
                  (f"Keep the requirements for {name} together. " * 12).strip()
                  for i, name in enumerate(columns[1:], start=1)]
    row = "| " + " | ".join(values) + " |"
    md = "# NJ comparison\n\n" + "\n".join([header, sep, row])
    assert len(row) > 1600
    chunks = chunk_markdown(md)
    assert chunks and all(chunk.chars <= 1600 for chunk in chunks)
    full = "\n".join(chunk.text for chunk in chunks)
    for i, name in enumerate(columns[1:], start=1):
        assert f"{name}:" in full
        assert f"{name}_VALUE_{i}" in full
        assert "Eligibility field: Asset limits" in full
    # This is a program-by-program transformation, not a horizontal row sliced
    # halfway through without headings.
    assert all("Eligibility field: Asset limits" in c.text for c in chunks)
