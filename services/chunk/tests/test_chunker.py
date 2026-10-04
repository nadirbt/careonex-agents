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


def test_long_section_splits_but_small_section_merges():
    chunks = chunk_markdown(DOC, target=1600, max_chars=2800, min_chars=200)
    mltss = [c for c in chunks if "MLTSS" in c.heading_path]
    assert len(mltss) >= 2
    assert all(c.chars <= 2800 for c in chunks)
    # "Statewide Respite Care Program" was tiny and got folded forward into MLTSS, keeping its title.
    assert any("**2026 Division of Aging Services Programs > Statewide Respite Care Program**" in c.text for c in mltss)
    assert not any(c.heading_path[-1:] == ["Statewide Respite Care Program"] for c in chunks)
    # Deterministic ids.
    assert chunk_markdown(DOC)[0].chunk_id == chunk_markdown(DOC)[0].chunk_id


def test_oversized_table_repeats_header():
    rows = "\n".join(f"| County {i} | 555-010{i % 10} | Office {i} street address that is fairly long |" for i in range(80))
    md = "# ADRC contacts\n\n| County | Phone | Address |\n| --- | --- | --- |\n" + rows + "\n"
    chunks = chunk_markdown(md, target=1200, max_chars=2000, min_chars=50)
    assert len(chunks) > 1
    assert all("| County | Phone | Address |" in c.text for c in chunks)
    assert all(c.chars <= 2000 + 200 for c in chunks)
