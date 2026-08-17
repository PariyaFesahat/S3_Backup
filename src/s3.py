def upload_backup_directory(self, backup_dir) -> bool:
    """
    Upload a backup directory to S3.

    S3 structure:

        <prefix>/<server>/<date>/<backup_directory>/

    The date comes from the directory mtime, which is the
    timestamp normally shown by `ls -l`.

    If the same backup directory already exists in S3,
    it is skipped and never overwritten.
    """

    backup_dir = backup_dir.resolve()

    # Get the directory timestamp shown by ls -l
    directory_mtime = backup_dir.stat().st_mtime

    backup_date = datetime.fromtimestamp(
        directory_mtime
    ).strftime("%Y-%m-%d")

    # Example:
    # postgres/server01/2026-08-17/back090087/
    backup_prefix = (
        f"{self.prefix}/"
        f"{self.server_name}/"
        f"{backup_date}/"
        f"{backup_dir.name}/"
    )

    print(
        f"Processing: {backup_dir.name} "
        f"(date: {backup_date})"
    )

    # Check ONLY this backup directory.
    response = self.client.list_objects_v2(
        Bucket=self.bucket,
        Prefix=backup_prefix,
        MaxKeys=1,
    )

    if "Contents" in response:
        print(
            f"Skipping: {backup_dir.name} "
            f"already exists in S3"
        )
        return False

    # Find all files inside the backup directory.
    files = [
        path
        for path in backup_dir.rglob("*")
        if path.is_file()
    ]

    if not files:
        print(
            f"WARNING: {backup_dir} contains no files"
        )
        return False

    # Upload all files while preserving their names
    # and subdirectory structure.
    for file_path in files:
        relative_path = file_path.relative_to(backup_dir)

        object_name = (
            f"{backup_prefix}"
            f"{relative_path.as_posix()}"
        )

        print(f"Uploading: {file_path}")
        print(
            f"       to: "
            f"s3://{self.bucket}/{object_name}"
        )

        self.client.upload_file(
            str(file_path),
            self.bucket,
            object_name,
        )

    print(
        f"Backup uploaded: "
        f"{backup_dir.name} -> {backup_date}"
    )

    return True