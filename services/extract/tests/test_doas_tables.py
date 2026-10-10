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
