import logging
from pathlib import Path
from typing import NamedTuple, Sequence

from .backup import get_backup_date
from .s3 import S3Client


logger = logging.getLogger(__name__)


class TargetSyncResult(NamedTuple):
    """Outcome of syncing one directory to one target."""

    target_name: str
    ok: bool
    detail: str = ""


class BackupManager:
    """Synchronizes local backup directories to a single S3 target."""

    def __init__(self, s3_client: S3Client):
        self.s3 = s3_client
        self.target_name = s3_client.target_name

    @staticmethod
    def is_ignored_file(file_path: Path) -> bool:
        name = file_path.name

        if name.endswith(".swp"):
            return True

        if name.endswith(".swo"):
            return True

        if name.endswith(".swn"):
            return True

        if name.endswith("~"):
            return True

        if name.startswith(".#"):
            return True

        return False

    def sync_directory(self, backup_dir: Path) -> None:
        backup_dir = backup_dir.resolve()

        if not backup_dir.exists():
            logger.warning(
                "Backup directory no longer exists: %s (target=%s)",
                backup_dir,
                self.target_name,
            )
            return

        if not backup_dir.is_dir():
            return

        backup_date = get_backup_date(
            backup_dir
        )

        prefix = self.s3.get_backup_prefix(
            backup_date=backup_date,
            backup_name=backup_dir.name,
        )

        logger.info(
            "Synchronizing: %s to target=%s",
            backup_dir,
            self.target_name,
        )

        logger.debug(
            "S3 prefix: %s (target=%s)",
            prefix,
            self.target_name,
        )

        local_files = {}

        for file_path in backup_dir.rglob("*"):

            if not file_path.is_file():
                continue

            if self.is_ignored_file(file_path):
                logger.debug(
                    "Ignoring temporary file: %s",
                    file_path,
                )
                continue

            relative_path = file_path.relative_to(
                backup_dir
            )

            local_files[
                relative_path.as_posix()
            ] = file_path

        s3_files = self.s3.list_files(
            prefix
        )

        # New / changed files
        for relative_path, file_path in local_files.items():

            object_key = (
                f"{prefix}{relative_path}"
            )

            s3_file = s3_files.get(
                relative_path
            )

            if s3_file is None:

                logger.info(
                    "Uploading new file: %s to target=%s",
                    relative_path,
                    self.target_name,
                )

                try:
                    self.s3.upload_file(
                        file_path,
                        object_key,
                    )

                except FileNotFoundError:
                    logger.warning(
                        "File disappeared before upload: %s (target=%s)",
                        file_path,
                        self.target_name,
                    )

                continue

            try:
                local_size = (
                    file_path.stat().st_size
                )

            except FileNotFoundError:
                logger.warning(
                    "File disappeared before checking: %s (target=%s)",
                    file_path,
                    self.target_name,
                )
                continue

            if local_size != s3_file["size"]:

                logger.info(
                    "Uploading changed file: %s to target=%s",
                    relative_path,
                    self.target_name,
                )

                try:
                    self.s3.upload_file(
                        file_path,
                        object_key,
                    )

                except FileNotFoundError:
                    logger.warning(
                        "File disappeared before upload: %s (target=%s)",
                        file_path,
                        self.target_name,
                    )

        # Deleted files
        for relative_path, s3_file in s3_files.items():

            if relative_path not in local_files:

                logger.info(
                    "Deleting removed file: %s from target=%s",
                    relative_path,
                    self.target_name,
                )

                self.s3.delete_object(
                    s3_file["key"]
                )

        logger.info(
            "Synchronization completed: %s target=%s",
            backup_dir.name,
            self.target_name,
        )


class MultiTargetBackupManager:
    """
    Fans one local source path out to every target it is mapped to.

    Each target is synchronized independently: a failure on one target
    is logged and recorded, but never stops (or rolls back) the others.
    """

    def __init__(self, managers: Sequence[BackupManager]):
        self.managers = list(managers)

    @property
    def target_names(self) -> list[str]:
        return [manager.target_name for manager in self.managers]

    def sync_directory(self, backup_dir: Path) -> list[TargetSyncResult]:

        results: list[TargetSyncResult] = []

        for manager in self.managers:

            try:
                manager.sync_directory(backup_dir)

            except Exception as exc:
                logger.exception(
                    "Sync failed target=%s dir=%s",
                    manager.target_name,
                    backup_dir,
                )
                results.append(
                    TargetSyncResult(
                        manager.target_name,
                        False,
                        str(exc),
                    )
                )
                continue

            logger.info(
                "Sync successful target=%s dir=%s",
                manager.target_name,
                backup_dir,
            )

            results.append(
                TargetSyncResult(manager.target_name, True)
            )

        return results
