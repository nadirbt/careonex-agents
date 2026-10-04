import json

from careonex_extract.extract import extract_all, text_key_for

HTML = b"<html><head><title>PACE</title></head><body><main><h1>PACE</h1><p>Program of All-inclusive Care for the Elderly serves people 55 and older who need nursing home level of care but can live safely at home with support.</p></main></body></html>"


def seed(s3, bucket, key, body, sha, kind, extra=None):
    s3.put_object(Bucket=bucket, Key=key, Body=body, Metadata={"sha256": sha})
    attrs = {"source_id": key.split("/")[1], "kind": kind, "program": "PACE", "year": 2026, **(extra or {})}
    s3.put_object(Bucket=bucket, Key=key + ".metadata.json", Body=json.dumps({"metadataAttributes": attrs}))


def test_text_key_layout():
    assert text_key_for("raw/nj_doas/nj_doas_pace.html") == "text/nj_doas/nj_doas_pace.html.md"


def test_extract_then_unchanged_then_changed(aws, tmp_path):
    bucket = "ac215-program-kb-test"
    aws.create_bucket(Bucket=bucket)
    seed(aws, bucket, "raw/nj_doas/nj_doas_pace.html", HTML, "sha-A", "html")

    s1 = extract_all(aws, bucket, snapshot_id="s1", data_dir=tmp_path)
    assert s1.counts == {"extracted": 1}
    text = aws.get_object(Bucket=bucket, Key="text/nj_doas/nj_doas_pace.html.md")["Body"].read().decode()
    assert text.startswith("# PACE")
    side = json.loads(aws.get_object(Bucket=bucket, Key="text/nj_doas/nj_doas_pace.html.md.metadata.json")["Body"].read())
    attrs = side["metadataAttributes"]
    assert attrs["program"] == "PACE" and attrs["derived_from"] == "raw/nj_doas/nj_doas_pace.html"
    assert attrs["source_sha256"] == "sha-A" and attrs["extractor"] == "markdownify"
    assert (tmp_path / "s1" / "extract.json").is_file()

    s2 = extract_all(aws, bucket, snapshot_id="s2")
    assert s2.counts == {"unchanged": 1}

    # Raw bytes change (new nonce) but content is identical: re-extracted, text NOT changed.
    seed(aws, bucket, "raw/nj_doas/nj_doas_pace.html", HTML.replace(b"<head>", b"<head><script>x=1</script>"), "sha-B", "html")
    s3_ = extract_all(aws, bucket, snapshot_id="s3")
    assert s3_.counts == {"extracted": 1}
    assert s3_.items[0].text_changed is False

    # Content changes: text_changed flips to True.
    seed(aws, bucket, "raw/nj_doas/nj_doas_pace.html", HTML.replace(b"55 and older", b"60 and older"), "sha-C", "html")
    s4 = extract_all(aws, bucket, snapshot_id="s4")
    assert s4.items[0].text_changed is True


def test_dry_run_writes_nothing(aws):
    bucket = "ac215-program-kb-test"
    aws.create_bucket(Bucket=bucket)
    seed(aws, bucket, "raw/nj_doas/nj_doas_pace.html", HTML, "sha-A", "html")
    s = extract_all(aws, bucket, snapshot_id="dry", dry_run=True)
    assert s.counts == {"dry-run": 1}
    keys = [o["Key"] for o in aws.list_objects_v2(Bucket=bucket)["Contents"]]
    assert not any(k.startswith("text/") or k.startswith("snapshots/") for k in keys)
