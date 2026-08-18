from pathlib import Path

from .backup import find_backup_directories
from .config import load_config
from .manager import BackupManager
from .s3 import S3Client
from .watcher import BackupWatcher


def main():
    config = load_config()

    source_dir = Path(
        config["backup"]["source_dir"]
    )

    s3 = S3Client(config)

    print(
        f"Checking S3 bucket: {s3.bucket}"
    )

    s3.check_bucket()

    print(
        "S3 bucket is accessible."
    )

    manager = BackupManager(s3)

    # Synchronize existing directories once
    # when the application starts.
    backup_directories = find_backup_directories(
        str(source_dir)
    )

    print(
        f"Found {len(backup_directories)} "
        f"existing backup directory(s)."
    )

    for backup_dir in backup_directories:
        manager.sync_directory(
            backup_dir
        )

    # Start continuous watcher.
    watcher = BackupWatcher(
        source_dir=str(source_dir),
        manager=manager,
    )

    watcher.start()


if __name__ == "__main__":
    main()