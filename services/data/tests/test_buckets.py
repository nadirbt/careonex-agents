from careonex_data import config
from careonex_data.buckets import ensure_bucket, probe


def test_ensure_creates_then_is_idempotent(aws):
    name = "careonex-program-kb-test"
    assert probe(aws, name) == "missing"

    first = ensure_bucket(aws, config.KNOWLEDGE, name, "us-east-1")
    assert first.created is True
    assert first.versioning == "Enabled"
    assert first.encryption == "AES256"
    assert first.public_access_blocked is True

    second = ensure_bucket(aws, config.KNOWLEDGE, name, "us-east-1")
    assert second.created is False
    assert second.exists is True
    assert probe(aws, name) == "owned"


def test_bucket_name_defaults_to_account_suffix(aws, monkeypatch):
    monkeypatch.delenv("CAREONEX_KB_BUCKET", raising=False)
    config.account_id.cache_clear()
    assert config.bucket_name(config.KNOWLEDGE) == "careonex-program-kb-123456789012"
    monkeypatch.setenv("CAREONEX_KB_BUCKET", "my-existing-bucket")
    assert config.bucket_name(config.KNOWLEDGE) == "my-existing-bucket"
