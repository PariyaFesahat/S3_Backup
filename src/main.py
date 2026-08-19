import logging
from pathlib import Path

from .backup import find_backup_directories
from .config import load_config
from .logging_config import setup_logging
from .manager import BackupManager
from .s3 import S3Client
from .watcher import BackupWatcher


logger = logging.getLogger(__name__)


def main():
    config = load_config()

    logging_config = config.get(
        "logging",
        {},
    )

    setup_logging(
        logging_config.get(
            "level",
            "INFO",
        )
    )

    logger.info("Starting S3 backup watcher")

    source_dir = Path(
        config["backup"]["source_dir"]
    )

    s3 = S3Client(config)

    logger.info(
        "Checking S3 bucket: %s",
        s3.bucket,
    )

    s3.check_bucket()

    logger.info(
        "S3 bucket is accessible."
    )

    manager = BackupManager(s3)

    backup_directories = find_backup_directories(
        str(source_dir)
    )

    logger.info(
        "Found %d existing backup directory(s).",
        len(backup_directories),
    )

    for backup_dir in backup_directories:
        manager.sync_directory(
            backup_dir
        )

    watcher_config = config.get(
        "watcher",
        {},
    )

    debounce_seconds = watcher_config.get(
        "debounce_seconds",
        5,
    )

    watcher = BackupWatcher(
        source_dir=str(source_dir),
        manager=manager,
        debounce_seconds=debounce_seconds,
    )

    watcher.start()


if __name__ == "__main__":
    main()