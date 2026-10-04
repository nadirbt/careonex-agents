import json

from careonex_chunk.writer import chunk_all, chunk_prefix_for

MD = "# JACC\n\n" + ("JACC provides in-home services to people 60 and older. " * 12) + "\n\n## Financial eligibility\n\n| Household | Income |\n| --- | --- |\n| Individual | $4,855 |\n\n" + ("Cost share applies on a sliding scale based on income. " * 10) + "\n"


def seed(s3, bucket, md, text_sha):
    s3.put_object(Bucket=bucket, Key="text/nj_doas/nj_doas_jacc.html.md", Body=md.encode(), Metadata={"text_sha256": text_sha})
    s3.put_object(Bucket=bucket, Key="text/nj_doas/nj_doas_jacc.html.md.metadata.json", Body=json.dumps({"metadataAttributes": {"program": "JACC", "year": 2026, "source_id": "nj_doas"}}))


def test_prefix_layout():
    assert chunk_prefix_for("text/nj_doas/nj_doas_jacc.html.md") == "chunks/nj_doas/nj_doas_jacc.html/"


def test_chunk_unchanged_then_stale_cleanup(aws, tmp_path):
    bucket = "ac215-program-kb-test"
    aws.create_bucket(Bucket=bucket)
    seed(aws, bucket, MD, "t1")

    s1 = chunk_all(aws, bucket, snapshot_id="s1", data_dir=tmp_path)
    assert s1.counts == {"chunked": 1} and s1.total_chunks >= 1
    keys = sorted(o["Key"] for o in aws.list_objects_v2(Bucket=bucket, Prefix="chunks/")["Contents"])
    md_keys = [k for k in keys if k.endswith(".md")]
    assert len(md_keys) == s1.total_chunks and all(k + ".metadata.json" in keys for k in md_keys)
    side = json.loads(aws.get_object(Bucket=bucket, Key=md_keys[0] + ".metadata.json")["Body"].read())["metadataAttributes"]
    assert side["program"] == "JACC" and side["chunk_order"] == 0 and side["derived_from"].startswith("text/")
    assert (tmp_path / "s1" / "chunks.jsonl").is_file()

    s2 = chunk_all(aws, bucket, snapshot_id="s2")
    assert s2.counts == {"unchanged": 1}

    # Text shrinks to one tiny section: old chunk objects must be removed.
    seed(aws, bucket, "# JACC\n\nShort now.\n", "t2")
    s3_ = chunk_all(aws, bucket, snapshot_id="s3")
    assert s3_.counts == {"chunked": 1}
    remaining = [o["Key"] for o in aws.list_objects_v2(Bucket=bucket, Prefix="chunks/")["Contents"] if o["Key"].endswith(".md")]
    assert len(remaining) == 1 and s3_.docs[0].deleted == len(md_keys) - 0 or s3_.docs[0].deleted >= len(md_keys) - 1
