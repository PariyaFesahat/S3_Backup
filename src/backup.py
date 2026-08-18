from datetime import datetime
from pathlib import Path


def find_backup_directories(source_dir: str) -> list[Path]:
    """
    Find all backup directories directly under source_dir.
    """
    backup_dir = Path(source_dir)

    if not backup_dir.exists():
        raise FileNotFoundError(
            f"Backup directory does not exist: {backup_dir}"
        )

    if not backup_dir.is_dir():
        raise NotADirectoryError(
            f"Backup path is not a directory: {backup_dir}"
        )

    return sorted(
        path
        for path in backup_dir.iterdir()
        if path.is_dir()
    )


def get_backup_date(backup_dir: Path) -> str:
    """
    Get the date from the directory mtime.

    This is the timestamp normally shown by `ls -l`.
    """
    mtime = backup_dir.stat().st_mtime

    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")