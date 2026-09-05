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
    model_config = ConfigDict(extra="forbid")

    path: str
    target_name: str
    destination_prefix: Optional[str] = None
    enabled: bool = True

    @field_validator("path", "target_name")
    @classmethod
    def _not_blank(cls, value: str, info) -> str:
        if not value or not value.strip():
            raise ValueError(f"'{info.field_name}' must not be empty")
        return value


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

        seen_paths: dict[str, str] = {}

        for mapping in self.mappings:

            if mapping.target_name not in target_names:
                known = ", ".join(sorted(target_names))
                raise ValueError(
                    f"Mapping for path '{mapping.path}' references "
                    f"unknown target '{mapping.target_name}'. "
                    f"Known targets: {known}"
                )

            if not mapping.enabled:
                continue

            resolved = str(Path(mapping.path).expanduser())

            if resolved in seen_paths:
                raise ValueError(
                    f"Path '{mapping.path}' is mapped to multiple "
                    f"targets ('{seen_paths[resolved]}', "
                    f"'{mapping.target_name}'); each path must map "
                    "to exactly one target"
                )

            seen_paths[resolved] = mapping.target_name

        active = [
            mapping
            for mapping in self.mappings
            if mapping.enabled
            and targets_by_name[mapping.target_name].enabled
        ]

        if not active:
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


def parse_config(raw: dict) -> AppConfig:
    raw = raw or {}

    if _is_legacy_format(raw):
        raw = _migrate_legacy_format(raw)

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
