from pathlib import Path


def find_backup_files(source_dir: str, file_pattern: str) -> list[Path]:
    """
    Find backup files in the configured directory.

    Args:
        source_dir: Directory containing PostgreSQL backups.
        file_pattern: File pattern, for example '*.dump'.

    Returns:
        A sorted list of backup file paths.
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

    files = [
        path
        for path in backup_dir.glob(file_pattern)
        if path.is_file()
    ]

    return sorted(files)