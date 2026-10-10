from careonex_retrieve.retriever import program_labels


def test_short_aliases_do_not_match_unrelated_programs():
    assert program_labels("private pay") == []  # must not match "VA"
    assert program_labels("VA benefits") == program_labels("va")
    assert program_labels("JACC eligibility") == program_labels("jacc")
    assert program_labels("MLTSS and Medicaid") == program_labels("mltss")
    assert program_labels("advice") == []


def test_old_unverified_side_by_side_chunks_are_suppressed():
    import boto3
    from botocore.stub import Stubber
    from careonex_retrieve.retriever import retrieve

    rt = boto3.client("bedrock-agent-runtime", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    unsafe_key = "s3://ac215-program-kb-1/chunks/nj_doas/nj_doas_programs_side_by_side_2026.pdf/0000.md"
    base = {"content": {"text": "income 4,855 or 2,982 without program names"},
            "location": {"type": "S3", "s3Location": {"uri": unsafe_key}},
            "metadata": {"program": "All DoAS programs", "title": "DoAS (2026)"}}
    verified = {**base, "metadata": {**base["metadata"], "table_verified": True},
                "content": {"text": "JACC Income: $4,855"}}
    with Stubber(rt) as stub:
        stub.add_response("retrieve", {"retrievalResults": [base, verified]})
        out = retrieve(rt, "KBTEST1234", "JACC income", top_k=2, latest_only=False)
    assert len(out.passages) == 1 and out.passages[0].table_verified
    assert "JACC" in out.passages[0].text
