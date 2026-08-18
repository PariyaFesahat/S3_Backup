from pathlib import Path

from .backup import get_backup_date
from .s3 import S3Client


class BackupManager:
    def __init__(self, s3_client: S3Client):
        self.s3 = s3_client

    def sync_directory(self, backup_dir: Path) -> None:
        """
        Synchronize one local backup directory with S3.
        """

        backup_dir = backup_dir.resolve()

        if not backup_dir.exists():
            print(
                f"Backup directory no longer exists: "
                f"{backup_dir}"
            )
            return

        if not backup_dir.is_dir():
            return

        backup_date = get_backup_date(backup_dir)

        prefix = self.s3.get_backup_prefix(
            backup_date=backup_date,
            backup_name=backup_dir.name,
        )

        print()
        print(
            f"Synchronizing: {backup_dir}"
        )
        print(
            f"S3 prefix: {prefix}"
        )

        # Local files
        local_files = {}

        for file_path in backup_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(
                    backup_dir
                )

                local_files[
                    relative_path.as_posix()
                ] = file_path

        # Existing S3 files
        s3_files = self.s3.list_files(prefix)

        # Upload new/changed files
        for relative_path, file_path in local_files.items():

            object_key = (
                f"{prefix}{relative_path}"
            )

            s3_file = s3_files.get(relative_path)

            # New file
            if s3_file is None:
                print(
                    f"Uploading new file: "
                    f"{relative_path}"
                )

                self.s3.upload_file(
                    file_path,
                    object_key,
                )

                continue

            # Check file size first.
            local_size = file_path.stat().st_size

            if local_size != s3_file["size"]:
                print(
                    f"Uploading changed file: "
                    f"{relative_path}"
                )

                self.s3.upload_file(
                    file_path,
                    object_key,
                )

        # Delete files that no longer exist locally
        for relative_path, s3_file in s3_files.items():

            if relative_path not in local_files:
                print(
                    f"Deleting removed file: "
                    f"{relative_path}"
                )

                self.s3.delete_object(
                    s3_file["key"]
                )

        print(
            f"Synchronization completed: "
            f"{backup_dir.name}"
        )