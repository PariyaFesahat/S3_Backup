import logging
import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "config.yaml"

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or invalid."""


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class S3TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    bucket: str
    region: str = "us-east-1"
    endpoint_url: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    profile: Optional[str] = None
    prefix: str = ""
    enabled: bool = True

    @field_validator("name", "bucket")
    @classmethod
    def _not_blank(cls, value: str, info) -> str:
        if not value or not value.strip():
            raise ValueError(f"'{info.field_name}' must not be empty")
        return value

    @model_validator(mode="after")
    def _check_credentials(self) -> "S3TargetConfig":
        has_key = bool(self.access_key_id)
        has_secret = bool(self.secret_access_key)

        if has_key != has_secret:
            raise ValueError(
                f"S3 target '{self.name}': access_key_id and "
                "secret_access_key must both be set, or both omitted "
                "(to use a profile or the default AWS credential chain)"
            )

        if self.profile and (has_key or has_secret):
            raise ValueError(
                f"S3 target '{self.name}': 'profile' cannot be combined "
                "with access_key_id/secret_access_key"
            )

        return self


class MappingConfig(BaseModel):
    """
    One local source path and the S3 target(s) it is backed up to.

    A path may fan out to any number of targets, either by listing them
    in 'target_names', or by repeating the same path in several mapping
    entries with different 'target_name' values. The only thing that is
    rejected is mapping the same path to the *same* target twice.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    target_name: Optional[str] = None
    target_names: Optional[list[str]] = None
    destination_prefix: Optional[str] = None
    enabled: bool = True

    @field_validator("path", "target_name")
    @classmethod
    def _not_blank(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return value

        if not value.strip():
            raise ValueError(f"'{info.field_name}' must not be empty")

        return value

    @field_validator("target_names")
    @classmethod
    def _names_not_blank(
        cls, values: Optional[list[str]]
    ) -> Optional[list[str]]:

        if values is None:
            return values

        if not values:
            raise ValueError(
                "'target_names' must list at least one target"
            )

        seen = set()

        for value in values:

            if not value or not value.strip():
                raise ValueError(
                    "'target_names' must not contain empty entries"
                )

            if value in seen:
                raise ValueError(
                    f"'target_names' lists target '{value}' twice"
                )

            seen.add(value)

        return values

    @model_validator(mode="after")
    def _require_exactly_one_form(self) -> "MappingConfig":
        if self.target_name is None and self.target_names is None:
            raise ValueError(
                f"Mapping for path '{self.path}' must set either "
                "'target_name' (one target) or 'target_names' "
                "(one or more targets)"
            )

        if self.target_name is not None and self.target_names is not None:
            raise ValueError(
                f"Mapping for path '{self.path}' sets both "
                "'target_name' and 'target_names'; use one or the other"
            )

        return self

    @property
    def resolved_target_names(self) -> list[str]:
        """Every target this mapping backs up to, in config order."""

        if self.target_names is not None:
            return list(self.target_names)

        return [self.target_name]


class RetentionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = 10
    cleanup_interval_hours: int = 24


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"


class WatcherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debounce_seconds: int = 5


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: ServerConfig
    targets: list[S3TargetConfig]
    mappings: list[MappingConfig]
    retention: RetentionConfig = RetentionConfig()
    logging: LoggingConfig = LoggingConfig()
    watcher: WatcherConfig = WatcherConfig()

    @field_validator("targets")
    @classmethod
    def _validate_targets(
        cls, targets: list[S3TargetConfig]
    ) -> list[S3TargetConfig]:

        if not targets:
            raise ValueError(
                "'targets' must contain at least one S3 target"
            )

        seen = set()

        for target in targets:
            if target.name in seen:
                raise ValueError(
                    f"Duplicate target name: '{target.name}'"
                )
            seen.add(target.name)

        return targets

    @field_validator("mappings")
    @classmethod
    def _validate_mappings_not_empty(
        cls, mappings: list[MappingConfig]
    ) -> list[MappingConfig]:

        if not mappings:
            raise ValueError(
                "'mappings' must contain at least one path mapping"
            )

        return mappings

    @model_validator(mode="after")
    def _cross_validate(self) -> "AppConfig":
        target_names = {target.name for target in self.targets}
        targets_by_name = {target.name: target for target in self.targets}

        # (resolved path, target name) pairs. A path may appear as many
        # times as there are targets; only the exact same pair twice is
        # an error, because that would back the same content up to the
        # same place twice.
        seen_pairs: set[tuple[str, str]] = set()

        active_pairs: list[tuple[str, str]] = []

        for mapping in self.mappings:

            for name in mapping.resolved_target_names:

                if name not in target_names:
                    known = ", ".join(sorted(target_names))
                    raise ValueError(
                        f"Mapping for path '{mapping.path}' references "
                        f"unknown target '{name}'. "
                        f"Known targets: {known}"
                    )

            if not mapping.enabled:
                continue

            resolved = str(Path(mapping.path).expanduser())

            for name in mapping.resolved_target_names:

                pair = (resolved, name)

                if pair in seen_pairs:
                    raise ValueError(
                        f"Path '{mapping.path}' is mapped to target "
                        f"'{name}' more than once; a path may map to "
                        "many targets, but only once to each"
                    )

                seen_pairs.add(pair)

                if targets_by_name[name].enabled:
                    active_pairs.append(pair)

        if not active_pairs:
            raise ValueError(
                "No active path -> target mappings; nothing to back up "
                "(check 'enabled' flags on targets and mappings)"
            )

        return self

    def target_for(self, name: str) -> S3TargetConfig:
        for target in self.targets:
            if target.name == name:
                return target

        raise KeyError(name)


def _interpolate_env_vars(value):
    if isinstance(value, str):

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)

            if var_name not in os.environ:
                raise ConfigError(
                    f"Config references environment variable "
                    f"'{var_name}' which is not set"
                )

            return os.environ[var_name]

        return _ENV_VAR_PATTERN.sub(_replace, value)

    if isinstance(value, dict):
        return {
            key: _interpolate_env_vars(val)
            for key, val in value.items()
        }

    if isinstance(value, list):
        return [_interpolate_env_vars(val) for val in value]

    return value


def _is_legacy_format(raw: dict) -> bool:
    return (
        "s3" in raw
        and "targets" not in raw
        and "mappings" not in raw
    )


def _migrate_legacy_format(raw: dict) -> dict:
    logger.warning(
        "Detected legacy single-bucket config format ('s3:' + "
        "'backup.source_dirs:'). Auto-migrating to 'targets'/"
        "'mappings'. This compatibility shim is deprecated -- "
        "please update config/config.yaml to the new format."
    )

    old_s3 = raw.get("s3", {}) or {}
    server_name = (raw.get("server", {}) or {}).get("name") or "default"

    target = {
        "name": server_name,
        "bucket": old_s3.get("bucket"),
        "region": old_s3.get("region", "us-east-1"),
        "endpoint_url": old_s3.get("endpoint_url"),
        "access_key_id": old_s3.get("access_key"),
        "secret_access_key": old_s3.get("secret_key"),
        "prefix": old_s3.get("prefix", ""),
        "enabled": True,
    }

    source_dirs = (raw.get("backup", {}) or {}).get("source_dirs", [])

    mappings = [
        {"path": path, "target_name": server_name, "enabled": True}
        for path in source_dirs
    ]

    migrated = dict(raw)
    migrated.pop("s3", None)
    migrated.pop("backup", None)
    migrated["targets"] = [target]
    migrated["mappings"] = mappings

    return migrated


def _is_fan_out_shorthand(raw: dict) -> bool:
    """
    'backup.source_dirs:' + 'targets:' with no explicit 'mappings:'
    means "back every source dir up to every target".
    """

    return (
        "targets" in raw
        and "mappings" not in raw
        and bool((raw.get("backup", {}) or {}).get("source_dirs"))
    )


def _expand_fan_out_shorthand(raw: dict) -> dict:
    source_dirs = (raw.get("backup", {}) or {}).get("source_dirs", [])

    all_target_names = [
        (target or {}).get("name")
        for target in (raw.get("targets") or [])
    ]

    target_names = [name for name in all_target_names if name]

    logger.info(
        "No 'mappings:' given; backing up %d source dir(s) to all "
        "%d target(s): %s",
        len(source_dirs),
        len(target_names),
        ", ".join(target_names),
    )

    expanded = dict(raw)
    expanded.pop("backup", None)
    expanded["mappings"] = [
        {
            "path": path,
            "target_names": list(target_names),
            "enabled": True,
        }
        for path in source_dirs
    ]

    return expanded


def parse_config(raw: dict) -> AppConfig:
    raw = raw or {}

    if _is_legacy_format(raw):
        raw = _migrate_legacy_format(raw)

    elif _is_fan_out_shorthand(raw):
        raw = _expand_fan_out_shorthand(raw)

    raw = _interpolate_env_vars(raw)

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration:\n{exc}") from exc


def load_config(config_file: Path = CONFIG_FILE) -> AppConfig:
    if not config_file.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_file}"
        )

    with config_file.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    return parse_config(raw)
