from datetime import datetime, timedelta

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from src.config import S3TargetConfig
from src.retention import TEHRAN_TZ, run_retention_cleanup
from src.s3 import S3Client


BUCKET = "test-bucket"
SERVER_NAME = "test-server"
PREFIX = "postgres/"
RETENTION_DAYS = 10


def _target(**overrides) -> S3TargetConfig:
    data = {
        "name": "primary",
        "bucket": BUCKET,
        "region": "us-east-1",
        "access_key_id": "AKIAFAKETESTKEY",
        "secret_access_key": "fakefakefakesecret",
        "prefix": PREFIX,
    }
    data.update(overrides)
    return S3TargetConfig(**data)


def _make_client(server_name: str = SERVER_NAME, **overrides) -> S3Client:
    return S3Client(target=_target(**overrides), server_name=server_name)


def _today() -> "datetime.date":
    return datetime.now(TEHRAN_TZ).date()


def _recent_date() -> str:
    return (_today() - timedelta(days=2)).isoformat()


def _expired_date() -> str:
    return (_today() - timedelta(days=RETENTION_DAYS + 5)).isoformat()


def _boundary_date() -> str:
    # Exactly at the cutoff -- must be kept, not deleted (cutoff is the
    # oldest date still within the retention window).
    return (_today() - timedelta(days=RETENTION_DAYS)).isoformat()


def _put(s3, key: str, body: bytes = b"data") -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)


def _list_all_keys(s3) -> list[str]:
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


@mock_aws
def test_backups_within_retention_are_kept():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    recent_key = f"{PREFIX}{SERVER_NAME}/{_recent_date()}/backup001/file.sql"
    _put(s3, recent_key)

    client = _make_client()
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    assert recent_key in _list_all_keys(s3)


@mock_aws
def test_boundary_date_is_kept():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    boundary_key = f"{PREFIX}{SERVER_NAME}/{_boundary_date()}/file.sql"
    _put(s3, boundary_key)

    client = _make_client()
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    assert boundary_key in _list_all_keys(s3)


@mock_aws
def test_expired_backups_are_deleted():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    expired_key = f"{PREFIX}{SERVER_NAME}/{_expired_date()}/backup001/file.sql"
    recent_key = f"{PREFIX}{SERVER_NAME}/{_recent_date()}/backup002/file.sql"
    _put(s3, expired_key)
    _put(s3, recent_key)

    client = _make_client()
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    remaining = _list_all_keys(s3)
    assert expired_key not in remaining
    assert recent_key in remaining


@mock_aws
def test_another_servers_backups_are_never_deleted():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    expired = _expired_date()
    our_key = f"{PREFIX}{SERVER_NAME}/{expired}/file.sql"
    other_key = f"{PREFIX}other-server/{expired}/file.sql"
    _put(s3, our_key)
    _put(s3, other_key)

    client = _make_client()
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    remaining = _list_all_keys(s3)
    assert our_key not in remaining
    assert other_key in remaining


@mock_aws
def test_malformed_date_folders_are_ignored():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    not_a_date_key = f"{PREFIX}{SERVER_NAME}/not-a-date/file.sql"
    invalid_calendar_key = f"{PREFIX}{SERVER_NAME}/2024-13-40/file.sql"
    short_key = f"{PREFIX}{SERVER_NAME}/2024-1-1/file.sql"
    _put(s3, not_a_date_key)
    _put(s3, invalid_calendar_key)
    _put(s3, short_key)

    client = _make_client()
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    remaining = _list_all_keys(s3)
    assert not_a_date_key in remaining
    assert invalid_calendar_key in remaining
    assert short_key in remaining


@mock_aws
def test_multiple_objects_under_expired_date_are_all_deleted():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    expired = _expired_date()
    expired_keys = [
        f"{PREFIX}{SERVER_NAME}/{expired}/backup00{i}/file.sql"
        for i in range(5)
    ]

    for key in expired_keys:
        _put(s3, key)

    client = _make_client()
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    remaining = _list_all_keys(s3)
    assert not any(key in remaining for key in expired_keys)


@mock_aws
def test_more_than_1000_objects_are_handled_correctly():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    expired = _expired_date()
    total_objects = 1500

    for i in range(total_objects):
        _put(s3, f"{PREFIX}{SERVER_NAME}/{expired}/file{i}.sql", b"")

    client = _make_client()
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    remaining = [
        key for key in _list_all_keys(s3) if f"/{expired}/" in key
    ]
    assert remaining == []


@mock_aws
def test_listing_failure_does_not_raise():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    client = _make_client()

    def _boom(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "ListObjectsV2",
        )

    client.client.get_paginator = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("paginator unavailable")
    )

    # Should log and return, not raise.
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)


@mock_aws
def test_delete_failure_does_not_raise_and_next_run_can_retry():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    expired = _expired_date()
    key = f"{PREFIX}{SERVER_NAME}/{expired}/file.sql"
    _put(s3, key)

    client = _make_client()

    def _boom(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "DeleteObjects",
        )

    client.client.delete_objects = _boom

    # Should not raise despite the delete failing.
    run_retention_cleanup(client, RETENTION_DAYS, SERVER_NAME)

    # Object is still there since the delete failed -- the next
    # scheduled run gets a chance to retry it.
    assert key in _list_all_keys(s3)


@mock_aws
def test_delete_objects_batch_reports_partial_errors():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)

    client = _make_client()

    keys = [f"{PREFIX}{SERVER_NAME}/2024-01-01/file{i}.sql" for i in range(3)]
    for key in keys:
        _put(s3, key)

    original_delete = client.client.delete_objects

    def _partial_failure(**kwargs):
        response = original_delete(**kwargs)
        response["Errors"] = [
            {"Key": keys[0], "Code": "AccessDenied", "Message": "denied"}
        ]
        return response

    client.client.delete_objects = _partial_failure

    deleted_count = client.delete_objects_batch(keys)

    assert deleted_count == len(keys) - 1
