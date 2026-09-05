import logging
from pathlib import Path

from .backup import find_backup_directories
from .config import AppConfig, ConfigError, MappingConfig, load_config
from .logging_config import setup_logging
from .manager import BackupManager
from .retention import RetentionScheduler
from .s3 import S3Client
from .watcher import BackupWatcher


logger = logging.getLogger(__name__)


def _build_active_runners(
    config: AppConfig,
) -> tuple[list[tuple[MappingConfig, BackupManager]], list[tuple[MappingConfig, str]]]:
    """
    Resolve each enabled mapping to a BackupManager bound to its own
    S3 target. Disabled mappings/targets and targets that fail to
    initialize are collected separately so the run can continue.
    """

    runners = []
    skipped = []

    for mapping in config.mappings:

        if not mapping.enabled:
            logger.info(
                "Skipping disabled mapping: %s -> %s",
                mapping.path,
                mapping.target_name,
            )
            skipped.append((mapping, "mapping disabled"))
            continue

        target = config.target_for(mapping.target_name)

        if not target.enabled:
            logger.info(
                "Skipping mapping '%s' -> target '%s' (target disabled)",
                mapping.path,
                target.name,
            )
            skipped.append((mapping, "target disabled"))
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
                "Failed to initialize S3 target '%s' for path '%s'; "
                "skipping this mapping",
                target.name,
                mapping.path,
            )
            skipped.append((mapping, "target initialization failed"))
            continue

        runners.append((mapping, BackupManager(s3_client)))

    return runners, skipped


def _run_initial_sync(
    runners: list[tuple[MappingConfig, BackupManager]],
) -> list[tuple[MappingConfig, str, str]]:
    """
    Perform the initial full sync for each active mapping.
    Returns a list of (mapping, status, detail) tuples, where status
    is "ok" or "error". A failure on one mapping does not stop the
    others.
    """

    results = []

    for mapping, manager in runners:

        source_dir = Path(mapping.path).resolve()

        logger.info(
            "Checking backup path: %s -> target '%s'",
            source_dir,
            mapping.target_name,
        )

        try:
            backup_directories = find_backup_directories(
                str(source_dir)
            )

        except (FileNotFoundError, NotADirectoryError) as exc:
            logger.error(
                "Path error for mapping '%s' -> '%s': %s",
                mapping.path,
                mapping.target_name,
                exc,
            )
            results.append((mapping, "error", str(exc)))
            continue

        logger.info(
            "Found %d backup directory(s) under %s",
            len(backup_directories),
            source_dir,
        )

        mapping_failed = False
        detail = ""

        for backup_dir in backup_directories:

            try:
                manager.sync_directory(backup_dir)

            except Exception as exc:
                logger.exception(
                    "Sync failed: %s -> target '%s'",
                    backup_dir,
                    mapping.target_name,
                )
                mapping_failed = True
                detail = str(exc)

        results.append(
            (mapping, "error" if mapping_failed else "ok", detail)
        )

    return results


def _build_retention_scheduler(
    config: AppConfig,
    runners: list[tuple[MappingConfig, BackupManager]],
) -> RetentionScheduler:
    """
    Build a retention scheduler covering every distinct S3 location
    the app actually backs up to. Mappings that share the same
    target/prefix (bucket + server prefix) are deduplicated so
    retention isn't run twice against the same objects.
    """

    unique_clients = {}

    for _mapping, manager in runners:
        s3_client = manager.s3
        key = (s3_client.bucket, s3_client.get_server_prefix())
        unique_clients.setdefault(key, s3_client)

    return RetentionScheduler(
        s3_clients=list(unique_clients.values()),
        retention_days=config.retention.days,
        server_name=config.server.name,
        interval_hours=config.retention.cleanup_interval_hours,
    )


def _log_summary(
    results: list[tuple[MappingConfig, str, str]],
    skipped: list[tuple[MappingConfig, str]],
) -> None:

    logger.info("=" * 60)
    logger.info("Backup summary:")

    for mapping, status, detail in results:
        symbol = "OK" if status == "ok" else "FAILED"
        suffix = f" ({detail})" if detail else ""
        logger.info(
            "  [%s] %s -> %s%s",
            symbol,
            mapping.path,
            mapping.target_name,
            suffix,
        )

    for mapping, reason in skipped:
        logger.info(
            "  [SKIPPED] %s -> %s (%s)",
            mapping.path,
            mapping.target_name,
            reason,
        )

    failed = sum(1 for _, status, _ in results if status == "error")
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
    # it never blocks it, and cleanup errors never stop it.
    # -------------------------------------------------

    retention_scheduler = _build_retention_scheduler(config, runners)
    retention_scheduler.start()

    # -------------------------------------------------
    # Start watcher
    # -------------------------------------------------

    watcher = BackupWatcher(
        path_managers=[
            (mapping.path, manager) for mapping, manager in runners
        ],
        debounce_seconds=config.watcher.debounce_seconds,
    )

    watcher.start()


if __name__ == "__main__":
    main()
