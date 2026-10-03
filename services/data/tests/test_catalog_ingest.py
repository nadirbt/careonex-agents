import json
from pathlib import Path

from careonex_data.catalog import load_catalog, metadata_attributes
from careonex_data.ingest import ingest

CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "ragfile_list.csv"


def test_catalog_loads_all_rows_with_layout():
    rows = load_catalog(CATALOG)
    assert len(rows) == 17
    mltss = next(r for r in rows if r.file_name == "nj_dmahs_mltss_application_guidance_2026.pdf")
    assert mltss.object_key == "raw/nj_dmahs/nj_dmahs_mltss_application_guidance_2026.pdf"
    assert mltss.sidecar_key.endswith(".metadata.json")
    assert mltss.year == 2026
    assert mltss.jurisdiction == "NJ"
    assert mltss.content_type == "application/pdf"


def test_sidecar_flags_estimated_dates():
    rows = {r.file_name: r for r in load_catalog(CATALOG)}
    jacc = metadata_attributes(rows["nj_doas_jacc.html"])["metadataAttributes"]
    assert jacc["effective_date_estimated"] is True  # effective == fetch date
    medicare = metadata_attributes(rows["medicare_home_health_services_coverage.html"])["metadataAttributes"]
    assert medicare["effective_date_estimated"] is True  # "set by hand"
    guide = metadata_attributes(rows["nj_dmahs_mltss_application_guidance_2026.pdf"])["metadataAttributes"]
    assert guide["effective_date_estimated"] is False
    assert guide["program"] == "NJ FamilyCare / Medicaid MLTSS"


def test_ingest_from_local_dir_is_idempotent(aws, tmp_path):
    bucket = "careonex-program-kb-test"
    aws.create_bucket(Bucket=bucket)
    rows = load_catalog(CATALOG)
    local = tmp_path / "mirror"
    for r in rows:
        (local / r.source_id).mkdir(parents=True, exist_ok=True)
        (local / r.source_id / r.file_name).write_bytes(f"fake {r.file_name}".encode())

    m1 = ingest(aws, bucket, CATALOG, snapshot_id="snap-1", local_dir=local, data_dir=tmp_path / "data")
    assert m1.counts == {"uploaded": 17}
    assert all(i.sha_matches_catalog is False for i in m1.items)  # fake bytes != catalog sha
    sidecar = json.loads(aws.get_object(Bucket=bucket, Key=m1.items[0].key + ".metadata.json")["Body"].read())
    assert "metadataAttributes" in sidecar
    assert (tmp_path / "data" / "snap-1" / "manifest.json").is_file()
    assert aws.head_object(Bucket=bucket, Key="snapshots/snap-1/manifest.json")

    m2 = ingest(aws, bucket, CATALOG, snapshot_id="snap-2", local_dir=local)
    assert m2.counts == {"unchanged": 17}


def test_ingest_dry_run_uploads_nothing(aws, tmp_path):
    bucket = "careonex-program-kb-test"
    aws.create_bucket(Bucket=bucket)
    local = tmp_path / "mirror"
    for r in load_catalog(CATALOG):
        (local / r.source_id).mkdir(parents=True, exist_ok=True)
        (local / r.source_id / r.file_name).write_bytes(b"x")
    m = ingest(aws, bucket, CATALOG, snapshot_id="dry", local_dir=local, dry_run=True)
    assert m.counts == {"dry-run": 17}
    assert "Contents" not in aws.list_objects_v2(Bucket=bucket)
