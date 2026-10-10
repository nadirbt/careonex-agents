import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.pop("AWS_PROFILE", None)


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")
