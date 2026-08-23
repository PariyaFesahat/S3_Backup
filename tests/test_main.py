from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from src.config import parse_config
from src.main import _build_active_runners, _run_initial_sync


def _write_backup_file(
    source_dir: Path, backup_name: str, file_name: str
) -> None:
    backup_dir = source_dir / backup_name
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / file_name).write_bytes(b"some-backup-data")


def _target(name: str, bucket: str, **overrides) -> dict:
    target = {
        "name": name,
        "bucket": bucket,
        "region": "us-east-1",
        "access_key_id": "AKIAFAKETESTKEY",
        "secret_access_key": "fakefakefakesecret",
    }
    target.update(overrides)
    return target


@mock_aws
def test_each_path_routes_to_its_assigned_target(tmp_path):
    dir_a = tmp_path / "source_a"
    dir_b = tmp_path / "source_b"

    _write_backup_file(dir_a, "backup001", "file1.txt")
    _write_backup_file(dir_b, "backup002", "file2.txt")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("target-a", "bucket-a"),
            _target("target-b", "bucket-b"),
        ],
        "mappings": [
            {"path": str(dir_a), "target_name": "target-a"},
            {"path": str(dir_b), "target_name": "target-b"},
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-b")

    runners, skipped = _build_active_runners(config)
    assert not skipped
    assert len(runners) == 2

    results = _run_initial_sync(runners)

    statuses = {mapping.target_name: status for mapping, status, _ in results}
    assert statuses == {"target-a": "ok", "target-b": "ok"}

    # file from dir_a only landed in bucket-a, not bucket-b
    keys_a = [
        obj["Key"]
        for obj in s3.list_objects_v2(Bucket="bucket-a").get("Contents", [])
    ]
    keys_b = [
        obj["Key"]
        for obj in s3.list_objects_v2(Bucket="bucket-b").get("Contents", [])
    ]

    assert any("file1.txt" in key for key in keys_a)
    assert not any("file1.txt" in key for key in keys_b)

    assert any("file2.txt" in key for key in keys_b)
    assert not any("file2.txt" in key for key in keys_a)


@mock_aws
def test_disabled_mapping_and_disabled_target_are_skipped(tmp_path):
    dir_a = tmp_path / "source_a"
    dir_b = tmp_path / "source_b"
    dir_c = tmp_path / "source_c"

    _write_backup_file(dir_a, "backup001", "file1.txt")
    _write_backup_file(dir_b, "backup002", "file2.txt")
    _write_backup_file(dir_c, "backup003", "file3.txt")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("target-a", "bucket-a"),
            _target("target-b", "bucket-b", enabled=False),
            _target("target-c", "bucket-c"),
        ],
        "mappings": [
            {"path": str(dir_a), "target_name": "target-a"},
            # target disabled -> mapping should be skipped
            {"path": str(dir_b), "target_name": "target-b"},
            # mapping itself disabled -> should be skipped
            {
                "path": str(dir_c),
                "target_name": "target-c",
                "enabled": False,
            },
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-c")

    runners, skipped = _build_active_runners(config)

    assert len(runners) == 1
    assert runners[0][0].target_name == "target-a"

    reasons = {mapping.path: reason for mapping, reason in skipped}
    assert reasons[str(dir_b)] == "target disabled"
    assert reasons[str(dir_c)] == "mapping disabled"

    results = _run_initial_sync(runners)
    assert len(results) == 1
    assert results[0][1] == "ok"


@mock_aws
def test_failing_upload_does_not_block_other_mappings(tmp_path):
    dir_a = tmp_path / "source_a"
    dir_b = tmp_path / "source_b"

    _write_backup_file(dir_a, "backup001", "file1.txt")
    _write_backup_file(dir_b, "backup002", "file2.txt")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("target-a", "bucket-a"),
            _target("target-b", "bucket-b"),
        ],
        "mappings": [
            {"path": str(dir_a), "target_name": "target-a"},
            {"path": str(dir_b), "target_name": "target-b"},
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-b")

    runners, skipped = _build_active_runners(config)
    assert not skipped
    assert len(runners) == 2

    # Simulate target-b's upload failing (e.g. transient S3 error)
    # while target-a keeps working.
    for mapping, manager in runners:
        if mapping.target_name == "target-b":

            def _boom(*args, **kwargs):
                raise RuntimeError("simulated upload failure")

            manager.s3.upload_file = _boom

    results = _run_initial_sync(runners)

    statuses = {mapping.target_name: status for mapping, status, _ in results}
    assert statuses["target-a"] == "ok"
    assert statuses["target-b"] == "error"

    # target-a's file still made it despite target-b's failure
    objects_a = s3.list_objects_v2(Bucket="bucket-a")
    assert objects_a.get("KeyCount", 0) >= 1

    objects_b = s3.list_objects_v2(Bucket="bucket-b")
    assert objects_b.get("KeyCount", 0) == 0


@mock_aws
def test_missing_bucket_is_skipped_not_fatal(tmp_path):
    dir_a = tmp_path / "source_a"
    dir_b = tmp_path / "source_b"

    _write_backup_file(dir_a, "backup001", "file1.txt")
    _write_backup_file(dir_b, "backup002", "file2.txt")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("target-a", "bucket-a"),
            _target("target-missing", "bucket-does-not-exist"),
        ],
        "mappings": [
            {"path": str(dir_a), "target_name": "target-a"},
            {"path": str(dir_b), "target_name": "target-missing"},
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    # bucket-does-not-exist intentionally not created

    runners, skipped = _build_active_runners(config)

    assert len(runners) == 1
    assert runners[0][0].target_name == "target-a"

    reasons = {mapping.path: reason for mapping, reason in skipped}
    assert reasons[str(dir_b)] == "target initialization failed"
