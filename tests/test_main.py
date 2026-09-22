from pathlib import Path

import boto3
from moto import mock_aws

from src.config import parse_config
from src.main import (
    _build_active_runners,
    _build_retention_scheduler,
    _run_initial_sync,
)
from src.watcher import BackupWatcher


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


def _break_upload(runner, target_name: str) -> None:
    for manager in runner.manager.managers:
        if manager.target_name == target_name:

            def _boom(*args, **kwargs):
                raise RuntimeError("simulated upload failure")

            manager.s3.upload_file = _boom


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

    statuses = {
        result.target_name: result.status for result in results
    }
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
def test_one_path_is_uploaded_to_every_enabled_target(tmp_path):
    source = tmp_path / "db_dump"

    _write_backup_file(source, "backup1", "dump.sql")

    raw = {
        "server": {"name": "commentify-db"},
        "targets": [
            _target("parspack", "c606586", prefix="devops/"),
            _target("hetzner", "tkhsrv", prefix="devops/"),
            _target("another-s3", "third-bucket", prefix="devops/"),
        ],
        "mappings": [
            {
                "path": str(source),
                "target_names": ["parspack", "hetzner", "another-s3"],
            }
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    for bucket in ("c606586", "tkhsrv", "third-bucket"):
        s3.create_bucket(Bucket=bucket)

    runners, skipped = _build_active_runners(config)

    assert not skipped
    # One source path -> one runner, three targets behind it.
    assert len(runners) == 1
    assert runners[0].target_names == [
        "parspack",
        "hetzner",
        "another-s3",
    ]

    results = _run_initial_sync(runners)

    assert {r.target_name: r.status for r in results} == {
        "parspack": "ok",
        "hetzner": "ok",
        "another-s3": "ok",
    }

    # Same content, same layout (prefix/server/date/backup), in every
    # bucket.
    for bucket in ("c606586", "tkhsrv", "third-bucket"):
        keys = [
            obj["Key"]
            for obj in s3.list_objects_v2(Bucket=bucket).get(
                "Contents", []
            )
        ]

        assert len(keys) == 1
        assert keys[0].startswith("devops/commentify-db/")
        assert keys[0].endswith("/backup1/dump.sql")


@mock_aws
def test_repeated_path_entries_share_one_runner(tmp_path):
    source = tmp_path / "db_dump"

    _write_backup_file(source, "backup1", "dump.sql")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("parspack", "bucket-a"),
            _target("hetzner", "bucket-b"),
        ],
        "mappings": [
            {"path": str(source), "target_name": "parspack"},
            {"path": str(source), "target_name": "hetzner"},
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-b")

    runners, skipped = _build_active_runners(config)

    assert not skipped
    assert len(runners) == 1
    assert runners[0].target_names == ["parspack", "hetzner"]

    _run_initial_sync(runners)

    for bucket in ("bucket-a", "bucket-b"):
        assert s3.list_objects_v2(Bucket=bucket).get("KeyCount", 0) == 1


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
            # target disabled -> this target should be skipped
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
    assert runners[0].target_names == ["target-a"]

    reasons = {item.path: item.reason for item in skipped}
    assert reasons[str(dir_b)] == "target disabled"
    assert reasons[str(dir_c)] == "mapping disabled"

    results = _run_initial_sync(runners)
    assert len(results) == 1
    assert results[0].status == "ok"


@mock_aws
def test_disabled_target_does_not_affect_others_on_same_path(tmp_path):
    source = tmp_path / "db_dump"

    _write_backup_file(source, "backup1", "dump.sql")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("parspack", "bucket-a"),
            _target("hetzner", "bucket-b", enabled=False),
        ],
        "mappings": [
            {
                "path": str(source),
                "target_names": ["parspack", "hetzner"],
            }
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-b")

    runners, skipped = _build_active_runners(config)

    assert len(runners) == 1
    assert runners[0].target_names == ["parspack"]
    assert [item.target_name for item in skipped] == ["hetzner"]

    results = _run_initial_sync(runners)

    assert [(r.target_name, r.status) for r in results] == [
        ("parspack", "ok")
    ]
    assert s3.list_objects_v2(Bucket="bucket-a").get("KeyCount", 0) == 1
    assert s3.list_objects_v2(Bucket="bucket-b").get("KeyCount", 0) == 0


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
    for runner in runners:
        _break_upload(runner, "target-b")

    results = _run_initial_sync(runners)

    statuses = {r.target_name: r.status for r in results}
    assert statuses["target-a"] == "ok"
    assert statuses["target-b"] == "error"

    # target-a's file still made it despite target-b's failure
    objects_a = s3.list_objects_v2(Bucket="bucket-a")
    assert objects_a.get("KeyCount", 0) >= 1

    objects_b = s3.list_objects_v2(Bucket="bucket-b")
    assert objects_b.get("KeyCount", 0) == 0


@mock_aws
def test_failure_on_one_target_does_not_block_the_other(tmp_path):
    source = tmp_path / "db_dump"

    _write_backup_file(source, "backup1", "dump.sql")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("parspack", "bucket-a"),
            _target("hetzner", "bucket-b"),
        ],
        "mappings": [
            {
                "path": str(source),
                "target_names": ["parspack", "hetzner"],
            }
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-b")

    runners, _ = _build_active_runners(config)

    # Hetzner fails; Parspack must still succeed and must not be rolled
    # back.
    _break_upload(runners[0], "hetzner")

    results = _run_initial_sync(runners)

    assert {r.target_name: r.status for r in results} == {
        "parspack": "ok",
        "hetzner": "error",
    }

    assert s3.list_objects_v2(Bucket="bucket-a").get("KeyCount", 0) == 1
    assert s3.list_objects_v2(Bucket="bucket-b").get("KeyCount", 0) == 0


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
    assert runners[0].target_names == ["target-a"]

    reasons = {item.path: item.reason for item in skipped}
    assert reasons[str(dir_b)] == "target initialization failed"


@mock_aws
def test_broken_target_does_not_remove_working_target_on_same_path(
    tmp_path,
):
    source = tmp_path / "db_dump"

    _write_backup_file(source, "backup1", "dump.sql")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("parspack", "bucket-a"),
            _target("hetzner", "bucket-does-not-exist"),
        ],
        "mappings": [
            {
                "path": str(source),
                "target_names": ["parspack", "hetzner"],
            }
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")

    runners, skipped = _build_active_runners(config)

    assert len(runners) == 1
    assert runners[0].target_names == ["parspack"]
    assert [(i.target_name, i.reason) for i in skipped] == [
        ("hetzner", "target initialization failed")
    ]


@mock_aws
def test_watcher_watches_each_source_path_once(tmp_path):
    source = tmp_path / "db_dump"

    _write_backup_file(source, "backup1", "dump.sql")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("parspack", "bucket-a"),
            _target("hetzner", "bucket-b"),
        ],
        "mappings": [
            {"path": str(source), "target_name": "parspack"},
            {"path": str(source), "target_name": "hetzner"},
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-b")

    runners, _ = _build_active_runners(config)

    watcher = BackupWatcher(
        path_managers=[
            (runner.path, runner.manager) for runner in runners
        ],
        debounce_seconds=1,
    )

    # Exactly one watched path, covering both targets.
    assert len(watcher.path_managers) == 1

    watched_dir, manager = watcher.path_managers[0]
    assert watched_dir == source.resolve()
    assert manager.target_names == ["parspack", "hetzner"]


def test_watcher_merges_duplicate_paths_from_separate_managers(tmp_path):
    source = tmp_path / "db_dump"
    source.mkdir()

    class _FakeManager:
        def __init__(self, name):
            self.target_name = name
            self.synced = []

        def sync_directory(self, backup_dir):
            self.synced.append(backup_dir)

    first = _FakeManager("parspack")
    second = _FakeManager("hetzner")

    watcher = BackupWatcher(
        path_managers=[(str(source), first), (str(source), second)],
        debounce_seconds=1,
    )

    assert len(watcher.path_managers) == 1

    _watched_dir, manager = watcher.path_managers[0]

    # A single filesystem event reaches every target.
    backup_dir = source / "backup1"
    results = manager.sync_directory(backup_dir)

    assert [r.target_name for r in results] == ["parspack", "hetzner"]
    assert first.synced == [backup_dir]
    assert second.synced == [backup_dir]


@mock_aws
def test_retention_runs_independently_per_target(tmp_path):
    source = tmp_path / "db_dump"

    _write_backup_file(source, "backup1", "dump.sql")

    raw = {
        "server": {"name": "test-server"},
        "targets": [
            _target("parspack", "bucket-a", prefix="devops/"),
            _target("hetzner", "bucket-b", prefix="devops/"),
        ],
        "mappings": [
            {
                "path": str(source),
                "target_names": ["parspack", "hetzner"],
            }
        ],
    }

    config = parse_config(raw)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-a")
    s3.create_bucket(Bucket="bucket-b")

    runners, _ = _build_active_runners(config)

    scheduler = _build_retention_scheduler(config, runners)

    # One retention client per target, each scoped to its own bucket.
    assert [client.target_name for client in scheduler.s3_clients] == [
        "parspack",
        "hetzner",
    ]
    assert [client.bucket for client in scheduler.s3_clients] == [
        "bucket-a",
        "bucket-b",
    ]
    assert all(
        client.get_server_prefix() == "devops/test-server/"
        for client in scheduler.s3_clients
    )
