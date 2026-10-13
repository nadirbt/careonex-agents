"""Exercise program-to-column alignment using generated, ruled PDF tables."""

import pytest
import pymupdf

from careonex_extract.doas_tables import doas_side_by_side_to_markdown
from careonex_extract.convert import to_markdown


FIRST = ("MLTSS/PACE", "JACC", "SRCP", "AADSP", "CHSP", "OAA")
SECOND = ("PAAD", "Senior Gold", "MSPs: QMB, SLMB, QI", "Lifeline", "HAAAD/NJHAP", "USF/LIHEAP")


def make_pdf(broken_header=False):
    doc = pymupdf.open()
    for n in range(4):
        page = doc.new_page(width=1100, height=400)
        x = [20, 180, 330, 480, 630, 780, 930, 1080]
        y = [30, 75, 115, 185]
        for xx in x:
            page.draw_line((xx, y[0]), (xx, y[-1]))
        for yy in y:
            page.draw_line((x[0], yy), (x[-1], yy))
        names = FIRST if n < 2 else SECOND
        # Headers in row 0 and program-specific values in rows 1 and 2.
        for idx, name in enumerate(("Program",) + names):
            if broken_header and n == 1 and idx == 2:
                name = "unlabeled"
            page.insert_text((x[idx] + 4, 54), name, fontsize=9)
        page.insert_text((x[0] + 4, 94), "Income", fontsize=9)
        page.insert_text((x[0] + 4, 134), "Age", fontsize=9)
        for idx, name in enumerate(names, 1):
            page.insert_text((x[idx] + 4, 94), f"${idx},123", fontsize=9)
            page.insert_text((x[idx] + 4, 134), f"{idx * 10} years", fontsize=9)
    return doc.tobytes()


def test_doas_tables_preserve_program_with_limits():
    md = doas_side_by_side_to_markdown(make_pdf())
    for program in FIRST + ("PAAD", "Senior Gold", "Lifeline"):
        assert f"### {program}" in md
    section = md.split("### JACC", 1)[1].split("### SRCP", 1)[0]
    assert "Income:** $2,123" in section
    assert "$3,123" not in section  # SRCP's value must NOT appear under JACC
    assert "Age:** 20 years" in section
    via_dispatch = to_markdown(make_pdf(), "pdf", "nj_doas_programs_side_by_side_2026.pdf")
    assert via_dispatch == md


def test_doas_table_fails_closed_when_column_heading_missing():
    with pytest.raises(ValueError, match="program columns could not be verified"):
        doas_side_by_side_to_markdown(make_pdf(broken_header=True))


def test_re_read_never_changes_digits_or_decimal_markers():
    from careonex_extract.doas_tables import _verified_cell_text

    class FakePage:
        def __init__(self, text):
            self.text = text
        def get_text(self, *args, **kwargs):
            return self.text

    box = (0, 0, 100, 100)
    # The extraction glitch is fixable without changing the digits.
    assert _verified_cell_text(FakePage("$2,123"), box, "$2123 ,") == "$2,123"
    assert _verified_cell_text(FakePage("20 years"), box, "20years") == "20 years"
    # But a competing or misleading read can never replace financial rules.
    assert _verified_cell_text(FakePage("$2,500"), box, "$2123 ,") == "$2123 ,"
    assert _verified_cell_text(FakePage("$12.34"), box, "$1234") == "$1234"
    assert _verified_cell_text(FakePage("under 10"), box, "over 10") == "over 10"


def test_split_program_headers_across_two_rows():
    """A split header is identified before any figures can be assigned."""
    from careonex_extract.doas_tables import _identify_headers
    names = ("PAAD", "Senior Gold", "MSPs (QMB/SLMB/QI)", "Lifeline", "HAAAD/NJHAP", "USF/LIHEAP")
    rows = [
        ["", "PAAD", "Senior", "MSPs:", "Lifeline", "HAAAD/", "USF/"],
        ["", "", "Gold", "QMB, SLMB, QI", "", "NJHAP", "LIHEAP"],
        ["Income", "$1", "$2", "$3", "$4", "$5", "$6"],
    ]
    assert _identify_headers(rows) == (names, 1)


def test_drop_only_truly_empty_spacer_columns_and_preserve_geometry():
    from careonex_extract.doas_tables import _remove_empty_spacer_columns, _identify_headers
    rows = [
        ["Program", "MLTSS/PACE", "", "JACC", "SRCP", "AADSP", "CHSP", "OAA", ""],
        ["Income", "$1", "", "$2", "$3", "$4", "$5", "$6", ""],
    ]
    cleaned, indices = _remove_empty_spacer_columns(rows)
    assert indices == (0, 1, 3, 4, 5, 6, 7)
    assert _identify_headers(cleaned) == (FIRST, 0)
    assert cleaned[1] == ["Income", "$1", "$2", "$3", "$4", "$5", "$6"]
    # A column with even one nonempty value is NOT safe to discard.
    rows[1][2] = "unidentified income"
    cleaned, _ = _remove_empty_spacer_columns(rows)
    assert _identify_headers(cleaned) is None


def test_real_official_pdf_when_supplied(monkeypatch):
    """Explicit offline acceptance test; requires official PDF bytes from the team.

    CAREONEX_DOAS_PDF_PATH points to the four-page March 2026 government PDF.
    Do not claim real-PDF verification when this test is skipped.
    """
    import os
    from pathlib import Path
    path = os.environ.get("CAREONEX_DOAS_PDF_PATH")
    if not path:
        pytest.skip("Set CAREONEX_DOAS_PDF_PATH to verify against the official PDF")
    md = doas_side_by_side_to_markdown(Path(path).read_bytes())
    page2 = md.split("## Page 2 (March 2026)", 1)[1].split("## Page 3 (March 2026)", 1)[0]
    jacc = page2.split("### JACC", 1)[1].split("### SRCP", 1)[0]
    srcp = page2.split("### SRCP", 1)[1].split("### AADSP", 1)[0]
    aadsp = page2.split("### AADSP", 1)[1].split("### CHSP", 1)[0]
    # These figures belong to different program columns on *page two*.
    assert "4,855" in jacc
    assert "2,982" in srcp
    assert "50,256" in aadsp
    assert "4,855" not in srcp
    assert "Up to $1,090/mo." in jacc
    assert "1,090" not in srcp and "1,090" not in aadsp
    assert "$6,582/mo." in jacc and "$5,964/mo." in srcp
    assert "$58,632/yr." in aadsp
    # Confirm all four pages (not just page 2), both families of 6 programs.
    assert all(f"## Page {n} (March 2026)" in md for n in range(1, 5))
    assert md.count("### JACC\n") == 2
    assert md.count("### PAAD\n") == 2
    assert "### HAAAD/NJHAP" in md
    assert "## Page 4 (March 2026)" in md
    assert "### MSPs (QMB/SLMB/QI)" in md
    page3 = md.split("## Page 3 (March 2026)", 1)[1].split("## Page 4 (March 2026)", 1)[0]
    assert "$64,943" in page3.split("### Senior Gold", 1)[1].split("### MSPs", 1)[0]
    assert "$15,960" in page3.split("### MSPs", 1)[1].split("### Lifeline", 1)[0]
    assert "$225 annual benefit" in page3.split("### Lifeline", 1)[1].split("### HAAAD", 1)[0]
    assert "$500 reimbursement" in page3.split("### HAAAD", 1)[1].split("### USF", 1)[0]
    page4 = md.split("## Page 4 (March 2026)", 1)[1]
    assert "NJAC 10:167B" in page4.split("### Senior Gold", 1)[1].split("### MSPs", 1)[0]
    from careonex_extract.convert import to_markdown
    assert to_markdown(Path(path).read_bytes(), "pdf", "nj_doas_programs_side_by_side_2026.pdf") == md
    # The public PDF has seven *logical* columns on every page, even though
    # the non-strict table detector spuriously reports nine on pages 1-2.
    doc = pymupdf.open(stream=Path(path).read_bytes(), filetype="pdf")
    assert len(doc) == 4
    for page in doc:
        tables = page.find_tables(strategy="lines_strict").tables
        assert len(tables) == 1 and tables[0].col_count == 7


def test_extractor_reads_split_headers_with_extra_ruled_spacer_column():
    """Integration: mapping must survive a geometrically detected blank column."""
    doc = pymupdf.open()
    for n in range(4):
        page = doc.new_page(width=1450, height=410)
        # 8 actual PDF columns: 7 logical columns + one entirely empty spacer.
        x = [20, 200, 375, 550, 725, 900, 1075, 1250, 1430]
        y = [35, 85, 130, 185, 250]
        for xx in x:
            page.draw_line((xx, y[0]), (xx, y[-1]))
        for yy in y:
            page.draw_line((x[0], yy), (x[-1], yy))
        names = FIRST if n < 2 else SECOND
        header = list(names)
        if n >= 2:
            header[1] = "Senior"  # Gold is on a separate PDF row
            header[2] = "MSPs:"   # QMB / SLMB / QI on the next row
        positions = [1, 2, 3, 4, 5, 6]  # the final eighth column is empty
        page.insert_text((x[0] + 4, 62), "Program", fontsize=10)
        for pos, value in zip(positions, header):
            page.insert_text((x[pos] + 5, 62), value, fontsize=9)
        if n >= 2:
            page.insert_text((x[2] + 5, 113), "Gold", fontsize=9)
            page.insert_text((x[3] + 5, 113), "QMB/SLMB/QI", fontsize=9)
        page.insert_text((x[0] + 5, 158), "Income", fontsize=9)
        for pos in positions:
            page.insert_text((x[pos] + 5, 158), f"${pos},123", fontsize=9)
    md = doas_side_by_side_to_markdown(doc.tobytes())
    jacc = md.split("### JACC", 1)[1].split("### SRCP", 1)[0]
    assert "Income:** $2,123" in jacc
    assert "Income:** $3,123" not in jacc
    assert "### Senior Gold" in md
    assert "### MSPs (QMB/SLMB/QI)" in md


def test_nine_detected_columns_with_split_program_cells_preserve_amounts():
    """Nine physical columns must map to seven logical program columns."""
    doc = pymupdf.open()
    for page_idx in range(4):
        page = doc.new_page(width=1600, height=430)
        x = [20 + 172 * idx for idx in range(10)]
        y = [30, 85, 160, 285, 375]
        for xx in x:
            page.draw_line((xx, y[0]), (xx, y[-1]))
        for yy in y:
            page.draw_line((x[0], yy), (x[-1], yy))
        if page_idx < 2:
            headers = ('Program', 'MLTSS/', 'PACE', 'JACC', 'SRCP', 'AADS', 'P', 'CHSP', 'OAA')
            vals = ('Income', 'a', 'b', '$4,855', '$2,982', '$50,', '256', 'CHSP-limit', 'OAA-limit')
        else:
            headers = ('Program', 'PA', 'AD', 'Senior Gold', 'MSPs: QMB/SLMB/QI', 'Lifeline', 'HAAAD/', 'NJHAP', 'USF/LIHEAP')
            vals = ('Income', 'a', 'b', '$9,999', '$3,333', '$4,444', '$5,', '555', '$6,666')
        for i, txt in enumerate(headers):
            page.insert_text((x[i] + 5, 56), txt, fontsize=9)
        for i, txt in enumerate(vals):
            page.insert_text((x[i] + 5, 112), txt, fontsize=9)
    md = doas_side_by_side_to_markdown(doc.tobytes())
    jacc = md.split('### JACC', 1)[1].split('### SRCP', 1)[0]
    srcp = md.split('### SRCP', 1)[1].split('### AADSP', 1)[0]
    aadsp = md.split('### AADSP', 1)[1].split('### CHSP', 1)[0]
    assert '$4,855' in jacc and '$2,982' not in jacc
    assert '$2,982' in srcp and '$4,855' not in srcp
    assert '$50, 256' in aadsp and '$4,855' not in aadsp
    assert '### Senior Gold' in md and '### HAAAD/NJHAP' in md


def test_nonempty_ninth_column_is_not_ignored_if_header_map_ambiguous():
    from careonex_extract.doas_tables import _map_logical_columns
    # Additional nonempty column with no matching heading could belong to
    # either JACC or SRCP. We cannot safely attach its financial value.
    rows = [
        ['Program', 'MLTSS/PACE', 'JACC', '', 'SRCP', 'AADSP', 'CHSP', 'OAA'],
        ['Income', '$1', '$2', 'suspicious $9,999', '$3', '$4', '$5', '$6'],
    ]
    assert _map_logical_columns(rows) is None
