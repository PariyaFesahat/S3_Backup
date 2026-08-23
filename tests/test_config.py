import pytest

from src.config import ConfigError, parse_config


def _base_raw(**overrides):
    raw = {
        "server": {"name": "test-server"},
        "targets": [
            {
                "name": "primary",
                "bucket": "my-bucket",
                "access_key_id": "AKIAFAKE",
                "secret_access_key": "fakefake",
            }
        ],
        "mappings": [
            {"path": "/data/a", "target_name": "primary"},
        ],
    }
    raw.update(overrides)
    return raw


def test_valid_config_parses():
    config = parse_config(_base_raw())

    assert config.targets[0].name == "primary"
    assert config.mappings[0].target_name == "primary"
    assert config.target_for("primary").bucket == "my-bucket"


def test_destination_prefix_is_optional():
    config = parse_config(_base_raw())

    assert config.mappings[0].destination_prefix is None


def test_unknown_target_reference_raises():
    raw = _base_raw(
        mappings=[{"path": "/data/a", "target_name": "does-not-exist"}]
    )

    with pytest.raises(ConfigError, match="unknown target"):
        parse_config(raw)


def test_duplicate_path_across_enabled_mappings_raises():
    raw = _base_raw(
        targets=[
            {
                "name": "primary",
                "bucket": "bucket-a",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
            {
                "name": "secondary",
                "bucket": "bucket-b",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
        ],
        mappings=[
            {"path": "/data/a", "target_name": "primary"},
            {"path": "/data/a", "target_name": "secondary"},
        ],
    )

    with pytest.raises(ConfigError, match="multiple targets"):
        parse_config(raw)


def test_duplicate_path_with_one_mapping_disabled_does_not_raise():
    raw = _base_raw(
        targets=[
            {
                "name": "primary",
                "bucket": "bucket-a",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
            {
                "name": "secondary",
                "bucket": "bucket-b",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
        ],
        mappings=[
            {"path": "/data/a", "target_name": "primary"},
            {
                "path": "/data/a",
                "target_name": "secondary",
                "enabled": False,
            },
        ],
    )

    config = parse_config(raw)

    assert len(config.mappings) == 2


def test_all_mappings_disabled_raises():
    raw = _base_raw(
        mappings=[
            {
                "path": "/data/a",
                "target_name": "primary",
                "enabled": False,
            }
        ]
    )

    with pytest.raises(ConfigError, match="No active"):
        parse_config(raw)


def test_all_mappings_pointing_to_disabled_target_raises():
    raw = _base_raw(
        targets=[
            {
                "name": "primary",
                "bucket": "my-bucket",
                "access_key_id": "k",
                "secret_access_key": "s",
                "enabled": False,
            }
        ],
    )

    with pytest.raises(ConfigError, match="No active"):
        parse_config(raw)


def test_credential_pairing_required():
    raw = _base_raw(
        targets=[
            {"name": "primary", "bucket": "b", "access_key_id": "only"}
        ]
    )

    with pytest.raises(ConfigError, match="must both be set"):
        parse_config(raw)


def test_profile_conflicts_with_keys():
    raw = _base_raw(
        targets=[
            {
                "name": "primary",
                "bucket": "b",
                "profile": "role",
                "access_key_id": "k",
                "secret_access_key": "s",
            }
        ]
    )

    with pytest.raises(ConfigError, match="cannot be combined"):
        parse_config(raw)


def test_duplicate_target_name_raises():
    raw = _base_raw(
        targets=[
            {
                "name": "primary",
                "bucket": "a",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
            {
                "name": "primary",
                "bucket": "b",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
        ]
    )

    with pytest.raises(ConfigError, match="Duplicate target name"):
        parse_config(raw)


def test_empty_targets_raises():
    raw = _base_raw(targets=[])

    with pytest.raises(ConfigError, match="at least one S3 target"):
        parse_config(raw)


def test_empty_mappings_raises():
    raw = _base_raw(mappings=[])

    with pytest.raises(ConfigError, match="at least one path mapping"):
        parse_config(raw)


def test_legacy_format_migrates(caplog):
    raw = {
        "server": {"name": "legacy-server"},
        "backup": {"source_dirs": ["/opt/test", "/opt/test2"]},
        "s3": {
            "endpoint_url": "http://localhost:9000",
            "access_key": "minioadmin",
            "secret_key": "minioadmin123",
            "bucket": "my-postgres-backups",
            "prefix": "postgres/",
            "region": "us-east-1",
        },
    }

    with caplog.at_level("WARNING"):
        config = parse_config(raw)

    assert len(config.targets) == 1
    assert config.targets[0].name == "legacy-server"
    assert config.targets[0].bucket == "my-postgres-backups"
    assert config.targets[0].access_key_id == "minioadmin"
    assert config.targets[0].secret_access_key == "minioadmin123"

    assert {m.path for m in config.mappings} == {
        "/opt/test",
        "/opt/test2",
    }
    assert all(m.target_name == "legacy-server" for m in config.mappings)

    assert any(
        "deprecated" in record.message for record in caplog.records
    )


def test_new_format_is_not_treated_as_legacy():
    raw = _base_raw()

    # Sanity check: presence of 'targets'/'mappings' should bypass
    # the legacy migration path entirely.
    config = parse_config(raw)

    assert config.targets[0].name == "primary"


def test_env_var_interpolation(monkeypatch):
    monkeypatch.setenv("TEST_SECRET", "s3cr3t")

    raw = _base_raw(
        targets=[
            {
                "name": "primary",
                "bucket": "b",
                "access_key_id": "AKIAFAKE",
                "secret_access_key": "${TEST_SECRET}",
            }
        ]
    )

    config = parse_config(raw)

    assert config.targets[0].secret_access_key == "s3cr3t"


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)

    raw = _base_raw(
        targets=[
            {
                "name": "primary",
                "bucket": "b",
                "access_key_id": "AKIAFAKE",
                "secret_access_key": "${MISSING_VAR}",
            }
        ]
    )

    with pytest.raises(ConfigError, match="MISSING_VAR"):
        parse_config(raw)
