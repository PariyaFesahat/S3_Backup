from pathlib import Path


def find_backup_directories(source_dir: str) -> list[Path]:
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