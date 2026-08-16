from .backup import find_backup_files
from .config import load_config
from .s3 import S3Client


def main():
    config = load_config()

    source_dir = config["backup"]["source_dir"]
    file_pattern = config["backup"]["file_pattern"]

    backup_files = find_backup_files(
        source_dir=source_dir,
        file_pattern=file_pattern,
    )

    print(f"Found {len(backup_files)} backup file(s):")

    for backup_file in backup_files:
        print(f"  {backup_file}")

    s3 = S3Client(config)

    print(f"Checking S3 bucket: {s3.bucket}")
    s3.check_bucket()

    for backup_file in backup_files:
        print(f"Uploading: {backup_file}")

        object_name = s3.upload_file(backup_file)

        print(f"Uploaded: {object_name}")

    print(
        f"Cleaning up backups older than "
        f"{config['retention']['days']} days..."
    )

    s3.cleanup_old_backups()

    print("Backup process completed.")


if __name__ == "__main__":
    main()