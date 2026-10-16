"""Regression for two-tier comparison tables from NJ DoAS PDF-to-Markdown export."""
from pathlib import Path

import pytest

from careonex_chunk.chunker import _split_table, chunk_markdown
from careonex_chunk.experiment import export
from careonex_chunk.hierarchical import hierarchical_chunk_markdown


def _two_tier_table(*, garbled_groups: bool) -> str:
    groups = (
        '|**2026**|**PRESCRIPTION PROG**|**NJ DEPART**|**MENT OF HUMAN**|**SERVICES(DHS)**|**HEARING**|**PROGRAMS**|'
        if garbled_groups else
        '|**2026**|**MEDICAID WAIVER PROGRAM**|||**NON-MEDICAID WAIVER**|**PROGRAMS**||'
    )
    if not garbled_groups:
        # The real source has exactly seven columns (not eight): note the trailing empty cell.
        groups = '|**2026**|**MEDICAID WAIVER PROGRAM**|||**NON-MEDICAID WAIVER**|**PROGRAMS**||'
        programs = '||**MLTSS/PACE**|**JACC**|**SRCP**|**AADSP**|**CHSP**|**OAA**|'
        titles = '|**Program Title**|**Managed long-term services**|**JACC description**|**Respite**|**Adult day**|**Housing**|**OAA**|'
        labels = ('MLTSS/PACE', 'JACC', 'SRCP', 'AADSP', 'CHSP', 'OAA')
    else:
        programs = '||**PAAD**|**Senior Gold**|**QMB, SLMB, QI**|**Lifeline**|**HAAAD/NJHAP**|**USF/LIHEAP**|'
        titles = '|**Program Title**|**Pharmacy**|**Senior Gold**|**Medicare Savings**|**Utility**|**Hearing**|**Energy**|'
        labels = ('PAAD', 'Senior Gold', 'QMB, SLMB, QI', 'Lifeline', 'HAAAD/NJHAP', 'USF/LIHEAP')
    body = '|**Financial Eligibility**|' + '|'.join(
        f'**{label}_AMOUNT_{i}** ' + ('Requirements stay with this program. ' * 12)
        for i, label in enumerate(labels)
    ) + '|'
    return '\n'.join((groups, '|---|---|---|---|---|---|---|', programs, titles, body))


@pytest.mark.parametrize('garbled', [False, True])
def test_two_level_pdf_header_uses_real_program_names(garbled):
    table = _two_tier_table(garbled_groups=garbled)
    chunks = chunk_markdown('# 2026 Programs\n\n' + table, target=900, max_chars=1100)
    combined = '\n'.join(c.text for c in chunks)
    assert all(c.chars <= 1100 for c in chunks)
    assert 'Financial Eligibility' in combined
    if garbled:
        assert '**PAAD**' in combined and '**Senior Gold**' in combined
        assert 'PAAD_AMOUNT_0' in combined and 'Senior Gold_AMOUNT_1' in combined
    else:
        assert '**MLTSS/PACE**:' in combined and '**JACC**:' in combined
        assert 'MLTSS/PACE_AMOUNT_0' in combined and 'JACC_AMOUNT_1' in combined
    assert 'Field:' in combined


def test_does_not_guess_column_names_for_ambiguous_table():
    table = '|Group|| |\n|---|---|---|\n|Not confirmed|foo|bar|\n|Eligibility|' + ('lots of words ' * 90) + '|val|'
    with pytest.raises(ValueError, match='missing column label'):
        _split_table(table, 250)


def test_real_exported_doas_source_across_three_strategies(tmp_path):
    # A real, minimally altered copy of the same source that failed on the user's machine.
    source = Path(__file__).parent / 'fixtures' / 'nj_doas_programs_side_by_side_2026.pdf.md'
    md = source.read_text(encoding='utf-8')
    result = chunk_markdown(md)
    hierarchy = hierarchical_chunk_markdown(md)
    assert result and hierarchy.children
    assert max(c.chars for c in result) <= 1600
    assert max(c.chunk.chars for c in hierarchy.children) <= 950
    all_text = '\n'.join(c.text for c in result)
    child_text = '\n'.join(c.chunk.text for c in hierarchy.children)
    for label in ('**MLTSS/PACE**', '**JACC**', '**PAAD**', '**Senior Gold**'):
        assert label in all_text, label
        assert label in child_text, label
    assert '**JACC**:' in all_text and '**JACC**:' in child_text
    assert '**PAAD**:' in child_text
    inp = tmp_path / 'input'
    inp.mkdir()
    (inp / 'comparison.md').write_text(md, encoding='utf-8')
    report = export(inp, tmp_path / 'out', 'doastable2026')
    assert not report['errors']
    assert len(report['results']) == 3
