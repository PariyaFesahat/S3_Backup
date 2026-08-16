import socket
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError


class S3Client:
    def __init__(self, config: dict):
        s3_config = config["s3"]

        self.bucket = s3_config["bucket"]
        self.prefix = s3_config.get("prefix", "").strip("/")

        self.retention_days = config["retention"]["days"]
        self.server_name = socket.gethostname()

        self.client = boto3.client(
            "s3",
            endpoint_url=s3_config["endpoint_url"],
            aws_access_key_id=s3_config["access_key"],
            aws_secret_access_key=s3_config["secret_key"],
            region_name=s3_config.get("region", "us-east-1"),
        )

    def check_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            raise RuntimeError(
                f"Cannot access S3 bucket '{self.bucket}': {exc}"
            ) from exc

    def upload_file(self, file_path) -> str:
        """Upload a backup under server/date directory."""
        file_path = file_path.resolve()

        backup_date = datetime.now().strftime("%Y-%m-%d")

        object_name = (
            f"{self.prefix}/"
            f"{self.server_name}/"
            f"{backup_date}/"
            f"{file_path.name}"
        )

        self.client.upload_file(
            str(file_path),
            self.bucket,
            object_name,
        )

        return object_name

    def cleanup_old_backups(self) -> None:
        cutoff_date = (
            datetime.now() - timedelta(days=self.retention_days)
        ).date()

        server_prefix = (
            f"{self.prefix}/"
            f"{self.server_name}/"
        )

        paginator = self.client.get_paginator("list_objects_v2")

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=server_prefix,
        ):
            for obj in page.get("Contents", []):
                object_key = obj["Key"]

                relative_path = object_key[len(server_prefix):]

                parts = relative_path.split("/")

                if len(parts) < 2:
                    continue

                date_folder = parts[0]

                try:
                    backup_date = datetime.strptime(
                        date_folder,
                        "%Y-%m-%d",
                    ).date()
                except ValueError:
                    continue

                if backup_date < cutoff_date:
                    print(f"Deleting old backup: {object_key}")

                    self.client.delete_object(
                        Bucket=self.bucket,
                        Key=object_key,
                    )