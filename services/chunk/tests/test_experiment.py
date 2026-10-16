"""Checks the true A/B/C variant export without AWS credentials."""
import json
from pathlib import Path

import pytest

from careonex_chunk import legacy_chunker
from careonex_chunk.chunker import chunk_markdown
from careonex_chunk.experiment import export, stage_upload
from careonex_chunk.hierarchical import hierarchical_chunk_markdown


def test_true_legacy_is_not_a_copy_of_section_aware():
    md = "# JACC\n\nTiny.\n\n# Medicaid\n\n" + "Medicaid services. " * 15
    a = legacy_chunker.chunk_markdown(md, min_chars=200)
    b = chunk_markdown(md, min_chars=200)
    assert [(x.text, x.heading_path) for x in a] != [(x.text, x.heading_path) for x in b]
    assert len(a) >= 1 and len(b) >= 1


def test_hierarchical_boundaries_and_parent_links():
    md = "# NJ\n\n## JACC\n\nEligibility: Age requirements.\n\n" + ("Details of income rules. " * 65) + "\n\nServices: Personal care assistance.\n\n## Medicare\n\nMedical coverage rules."
    result = hierarchical_chunk_markdown(md, parent_max_chars=550, child_target_chars=175, overlap_chars=25)
    assert len(result.parents) > 2 and len(result.children) > len(result.parents)
    parent_by_id = {p.parent_id: p for p in result.parents}
    assert len(parent_by_id) == len(result.parents)
    assert all(child.parent_id in parent_by_id for child in result.children)
    assert all(c.chunk.chars <= 175 for c in result.children)
    assert all(p.chars <= 550 for p in result.parents)
    assert all("Medicare" not in c.chunk.text for c in result.children if c.chunk.heading_path[-1] == "JACC")
    assert any(c.topic == "eligibility" for c in result.children)
    assert any(c.topic == "services" for c in result.children)
    assert result == hierarchical_chunk_markdown(md, parent_max_chars=550, child_target_chars=175, overlap_chars=25)


def test_table_label_safeguards():
    md = "# County contacts\n\n| County | Phone |\n| --- | --- |\n" + "\n".join(f"| County {i} | 555-01{i:02d} |" for i in range(25))
    result = hierarchical_chunk_markdown(md, parent_max_chars=320, child_target_chars=240)
    assert len(result.children) >= 2
    assert all("| County | Phone |" in c.chunk.text for c in result.children)
    assert all(c.chunk.chars <= 240 for c in result.children)
    assert sum(c.chunk.text.count("| County 0 |") for c in result.children) == 1


def test_export_three_variants_isolated_and_sidecars(tmp_path):
    source = tmp_path / "input" / "doc_group"
    source.mkdir(parents=True)
    doc = source / "data.md"
    doc.write_text("# NJ home care\n\n## JACC\n\nEligibility: Older adults.\n\nServices: Support.\n\n## Medicare\n\nCoverage differs.", encoding="utf8")
    (source / "data.md.metadata.json").write_text(json.dumps({"metadataAttributes": {"source_id": "state_guide", "program": "JACC", "year": 2026}}))
    output = tmp_path / "export"
    report = export(tmp_path / "input", output, "testrun10")
    assert not report["errors"] and len(report["results"]) == 3
    for strategy in ("legacy", "section", "hierarchical"):
        children = list((output / "strategies" / strategy / "chunks").rglob("*.md"))
        assert children
        for c in children:
            attrs = json.loads(Path(str(c) + ".metadata.json").read_text())["metadataAttributes"]
            assert attrs["strategy"] == strategy and attrs["year"] == 2026
            assert len(Path(str(c) + ".metadata.json").read_bytes()) < 1024
    hierarchical_base = output / "strategies" / "hierarchical"
    parents = list((hierarchical_base / "parents").rglob("*.md"))
    assert parents
    ids = {p.stem for p in parents}
    for child in (hierarchical_base / "chunks").rglob("*.md"):
        attrs = json.loads(Path(str(child) + ".metadata.json").read_text())["metadataAttributes"]
        assert attrs["parent_id"] in ids
    with pytest.raises(ValueError, match="confirm-upload"):
        stage_upload(output, "ac215-test-bucket")
    with pytest.raises(ValueError, match="staging bucket"):
        stage_upload(output, "production-kb", confirm=True)
    with pytest.raises(ValueError, match="must be empty"):
        export(tmp_path / "input", output, "testrun10")


def test_export_handles_wide_program_comparison_without_dropping_document(tmp_path):
    input_dir = tmp_path / "src"
    input_dir.mkdir()
    programs = ["MLTSS", "JACC", "PACE", "Medicare", "VA", "Respite"]
    header = "| Field | " + " | ".join(programs) + " |"
    separator = "| --- | " + " | ".join("---" for _ in programs) + " |"
    row = "| Financial guidelines | " + " | ".join(
        f"{program} condition: " + ("Details are assessed separately. " * 15)
        for program in programs) + " |"
    (input_dir / "comparison.md").write_text(
        "# New Jersey program comparison\n\n" + "\n".join([header, separator, row]), encoding="utf8"
    )
    output = tmp_path / "variants"
    manifest = export(input_dir, output, "testwide2026")
    assert not manifest["errors"]
    assert len(manifest["results"]) == 3
    for strategy in ("section", "hierarchical"):
        files = list((output / "strategies" / strategy / "chunks").rglob("*.md"))
        content = "\n".join(f.read_text(encoding="utf8") for f in files)
        assert files
        for program in programs:
            assert f"{program} condition:" in content
        assert all(len(f.read_text(encoding="utf8")) <= (950 if strategy == "hierarchical" else 1600)
                   for f in files)
