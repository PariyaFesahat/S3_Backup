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
    # Load configuration
    config = load_config()

    # Configure logging
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

    logger.info(
        "Starting S3 backup watcher"
    )

    # Get multiple backup source directories
    source_dirs = [
        Path(path).resolve()
        for path in config["backup"]["source_dirs"]
    ]

    # Create S3 client
    s3 = S3Client(config)

    logger.info(
        "Checking S3 bucket: %s",
        s3.bucket,
    )

    s3.check_bucket()

    logger.info(
        "S3 bucket is accessible."
    )

    # Create backup manager
    manager = BackupManager(s3)

    # -------------------------------------------------
    # Initial synchronization
    # -------------------------------------------------

    for source_dir in source_dirs:

        logger.info(
            "Checking backup directory: %s",
            source_dir,
        )

        backup_directories = (
            find_backup_directories(
                str(source_dir)
            )
        )

        logger.info(
            "Found %d backup directory(s) under %s",
            len(backup_directories),
            source_dir,
        )

        for backup_dir in backup_directories:

            manager.sync_directory(
                backup_dir
            )

    # -------------------------------------------------
    # Watcher configuration
    # -------------------------------------------------

    watcher_config = config.get(
        "watcher",
        {},
    )

    debounce_seconds = watcher_config.get(
        "debounce_seconds",
        5,
    )

    # -------------------------------------------------
    # Start watcher
    # -------------------------------------------------

    watcher = BackupWatcher(
        source_dirs=[
            str(path)
            for path in source_dirs
        ],
        manager=manager,
        debounce_seconds=debounce_seconds,
    )

    watcher.start()


if __name__ == "__main__":
    main()