from .backup import find_backup_directories
from .config import load_config
from .s3 import S3Client


def main():
    config = load_config()

    source_dir = config["backup"]["source_dir"]

    backup_directories = find_backup_directories(
        source_dir=source_dir,
    )

    print(
        f"Found {len(backup_directories)} backup "
        f"directory(s):"
    )

    for backup_dir in backup_directories:
        print(f"  {backup_dir}")

    s3 = S3Client(config)

    print(f"Checking S3 bucket: {s3.bucket}")
    s3.check_bucket()

    print("S3 bucket is accessible.")

    for backup_dir in backup_directories:
        s3.upload_backup_directory(backup_dir)

    print("Backup process completed.")


if __name__ == "__main__":
    main()