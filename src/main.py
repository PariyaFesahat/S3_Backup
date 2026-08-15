from .backup import find_backup_files
from .config import load_config


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


if __name__ == "__main__":
    main()