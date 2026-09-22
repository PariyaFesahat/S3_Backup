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


def test_same_path_can_map_to_multiple_targets():
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

    config = parse_config(raw)

    assert [m.target_name for m in config.mappings] == [
        "primary",
        "secondary",
    ]


def test_target_names_list_maps_one_path_to_many_targets():
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
            {
                "path": "/data/a",
                "target_names": ["primary", "secondary"],
            }
        ],
    )

    config = parse_config(raw)

    assert config.mappings[0].resolved_target_names == [
        "primary",
        "secondary",
    ]


def test_same_path_to_same_target_twice_raises():
    raw = _base_raw(
        mappings=[
            {"path": "/data/a", "target_name": "primary"},
            {"path": "/data/a", "target_name": "primary"},
        ],
    )

    with pytest.raises(ConfigError, match="more than once"):
        parse_config(raw)


def test_mapping_without_any_target_raises():
    raw = _base_raw(mappings=[{"path": "/data/a"}])

    with pytest.raises(ConfigError, match="must set either"):
        parse_config(raw)


def test_mapping_with_both_target_forms_raises():
    raw = _base_raw(
        mappings=[
            {
                "path": "/data/a",
                "target_name": "primary",
                "target_names": ["primary"],
            }
        ]
    )

    with pytest.raises(ConfigError, match="use one or the other"):
        parse_config(raw)


def test_unknown_target_in_target_names_raises():
    raw = _base_raw(
        mappings=[
            {
                "path": "/data/a",
                "target_names": ["primary", "nope"],
            }
        ]
    )

    with pytest.raises(ConfigError, match="unknown target"):
        parse_config(raw)


def test_source_dirs_without_mappings_fans_out_to_all_targets():
    raw = {
        "server": {"name": "test-server"},
        "backup": {"source_dirs": ["/db_dump", "/var/dumps"]},
        "targets": [
            {
                "name": "parspack",
                "bucket": "c606586",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
            {
                "name": "hetzner",
                "bucket": "tkhsrv",
                "access_key_id": "k",
                "secret_access_key": "s",
            },
        ],
    }

    config = parse_config(raw)

    assert [m.path for m in config.mappings] == [
        "/db_dump",
        "/var/dumps",
    ]
    assert all(
        m.resolved_target_names == ["parspack", "hetzner"]
        for m in config.mappings
    )


def test_disabled_mapping_for_same_path_still_parses():
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
