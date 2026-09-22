import logging
from pathlib import Path
from typing import NamedTuple

from .backup import find_backup_directories
from .config import AppConfig, ConfigError, load_config
from .logging_config import setup_logging
from .manager import BackupManager, MultiTargetBackupManager
from .retention import RetentionScheduler
from .s3 import S3Client
from .watcher import BackupWatcher


logger = logging.getLogger(__name__)


class SourceRunner(NamedTuple):
    """One local source path plus every target it fans out to."""

    path: str
    source_dir: Path
    manager: MultiTargetBackupManager

    @property
    def target_names(self) -> list[str]:
        return self.manager.target_names


class SkippedTarget(NamedTuple):
    path: str
    target_name: str
    reason: str


class SyncOutcome(NamedTuple):
    path: str
    target_name: str
    status: str  # "ok" | "error"
    detail: str


def _build_active_runners(
    config: AppConfig,
) -> tuple[list[SourceRunner], list[SkippedTarget]]:
    """
    Resolve the configured mappings into one runner per source path,
    each holding a BackupManager for every enabled target that path is
    mapped to. Disabled mappings/targets and targets that fail to
    initialize are collected separately so the run can continue with
    whatever is left.
    """

    managers_by_path: dict[Path, list[BackupManager]] = {}
    configured_path: dict[Path, str] = {}
    seen_pairs: set[tuple[Path, str]] = set()

    skipped: list[SkippedTarget] = []

    for mapping in config.mappings:

        source_dir = Path(mapping.path).expanduser().resolve()
        configured_path.setdefault(source_dir, mapping.path)

        if not mapping.enabled:
            for target_name in mapping.resolved_target_names:
                logger.info(
                    "Skipping disabled mapping: %s -> target=%s",
                    mapping.path,
                    target_name,
                )
                skipped.append(
                    SkippedTarget(
                        mapping.path, target_name, "mapping disabled"
                    )
                )
            continue

        for target_name in mapping.resolved_target_names:

            target = config.target_for(target_name)

            if not target.enabled:
                logger.info(
                    "Skipping %s -> target=%s (target disabled)",
                    mapping.path,
                    target.name,
                )
                skipped.append(
                    SkippedTarget(
                        mapping.path, target.name, "target disabled"
                    )
                )
                continue

            if (source_dir, target.name) in seen_pairs:
                logger.warning(
                    "Ignoring duplicate mapping: %s -> target=%s",
                    mapping.path,
                    target.name,
                )
                skipped.append(
                    SkippedTarget(
                        mapping.path, target.name, "duplicate mapping"
                    )
                )
                continue

            try:
                s3_client = S3Client(
                    target=target,
                    server_name=config.server.name,
                    prefix_override=mapping.destination_prefix,
                )
                s3_client.check_bucket()

            except Exception:
                logger.exception(
                    "Failed to initialize target=%s for path '%s'; "
                    "skipping this target (other targets continue)",
                    target.name,
                    mapping.path,
                )
                skipped.append(
                    SkippedTarget(
                        mapping.path,
                        target.name,
                        "target initialization failed",
                    )
                )
                continue

            seen_pairs.add((source_dir, target.name))

            managers_by_path.setdefault(source_dir, []).append(
                BackupManager(s3_client)
            )

    runners = [
        SourceRunner(
            path=configured_path[source_dir],
            source_dir=source_dir,
            manager=MultiTargetBackupManager(managers),
        )
        for source_dir, managers in managers_by_path.items()
    ]

    return runners, skipped


def _run_initial_sync(
    runners: list[SourceRunner],
) -> list[SyncOutcome]:
    """
    Perform the initial full sync for each source path, to every target
    that path is mapped to. Returns one outcome per (path, target). A
    failure on one target never stops the other targets, nor the other
    paths.
    """

    results: list[SyncOutcome] = []

    for runner in runners:

        logger.info(
            "Checking backup path: %s -> targets: %s",
            runner.source_dir,
            ", ".join(runner.target_names),
        )

        try:
            backup_directories = find_backup_directories(
                str(runner.source_dir)
            )

        except (FileNotFoundError, NotADirectoryError) as exc:
            logger.error(
                "Path error for '%s': %s",
                runner.path,
                exc,
            )
            results.extend(
                SyncOutcome(
                    runner.path, target_name, "error", str(exc)
                )
                for target_name in runner.target_names
            )
            continue

        logger.info(
            "Found %d backup directory(s) under %s",
            len(backup_directories),
            runner.source_dir,
        )

        failures: dict[str, str] = {}

        for backup_dir in backup_directories:

            for result in runner.manager.sync_directory(backup_dir):

                if not result.ok:
                    failures.setdefault(
                        result.target_name, result.detail
                    )

        results.extend(
            SyncOutcome(
                runner.path,
                target_name,
                "error" if target_name in failures else "ok",
                failures.get(target_name, ""),
            )
            for target_name in runner.target_names
        )

    return results


def _build_retention_scheduler(
    config: AppConfig,
    runners: list[SourceRunner],
) -> RetentionScheduler:
    """
    Build a retention scheduler covering every distinct S3 location the
    app actually backs up to, so retention runs independently for each
    target. Clients pointing at the same endpoint/bucket/server prefix
    (e.g. two source paths sharing one target) are deduplicated so
    retention isn't run twice against the same objects.
    """

    unique_clients = {}

    for runner in runners:
        for manager in runner.manager.managers:

            s3_client = manager.s3

            key = (
                s3_client.endpoint_url,
                s3_client.bucket,
                s3_client.get_server_prefix(),
            )

            unique_clients.setdefault(key, s3_client)

    return RetentionScheduler(
        s3_clients=list(unique_clients.values()),
        retention_days=config.retention.days,
        server_name=config.server.name,
        interval_hours=config.retention.cleanup_interval_hours,
    )


def _log_summary(
    results: list[SyncOutcome],
    skipped: list[SkippedTarget],
) -> None:

    logger.info("=" * 60)
    logger.info("Backup summary:")

    for path, target_name, status, detail in results:
        symbol = "OK" if status == "ok" else "FAILED"
        suffix = f" ({detail})" if detail else ""
        logger.info(
            "  [%s] %s -> target=%s%s",
            symbol,
            path,
            target_name,
            suffix,
        )

    for path, target_name, reason in skipped:
        logger.info(
            "  [SKIPPED] %s -> target=%s (%s)",
            path,
            target_name,
            reason,
        )

    failed = sum(1 for result in results if result.status == "error")
    succeeded = len(results) - failed

    logger.info(
        "%d succeeded, %d failed, %d skipped",
        succeeded,
        failed,
        len(skipped),
    )
    logger.info("=" * 60)


def main():
    try:
        config = load_config()

    except (FileNotFoundError, ConfigError) as exc:
        logging.basicConfig(level="ERROR")
        logger.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    setup_logging(config.logging.level)

    logger.info("Starting S3 backup watcher")

    runners, skipped = _build_active_runners(config)

    if not runners:
        raise RuntimeError(
            "No active path -> target mappings could be initialized; "
            "aborting"
        )

    # -------------------------------------------------
    # Initial synchronization
    # -------------------------------------------------

    results = _run_initial_sync(runners)

    _log_summary(results, skipped)

    # -------------------------------------------------
    # Retention cleanup
    #
    # Runs on its own background thread: once immediately, then every
    # `cleanup_interval_hours`. Fully independent from the watcher --
    # it never blocks it, and cleanup errors never stop it. Each target
    # is cleaned up independently.
    # -------------------------------------------------

    retention_scheduler = _build_retention_scheduler(config, runners)
    retention_scheduler.start()

    # -------------------------------------------------
    # Start watcher
    #
    # One watcher per source path, regardless of how many targets that
    # path fans out to.
    # -------------------------------------------------

    watcher = BackupWatcher(
        path_managers=[
            (runner.path, runner.manager) for runner in runners
        ],
        debounce_seconds=config.watcher.debounce_seconds,
    )

    watcher.start()


if __name__ == "__main__":
    main()
