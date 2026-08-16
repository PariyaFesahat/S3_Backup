import socket
from datetime import datetime

import boto3
from botocore.exceptions import ClientError


class S3Client:
    def __init__(self, config: dict):
        s3_config = config["s3"]

        self.bucket = s3_config["bucket"]
        self.prefix = s3_config.get("prefix", "").strip("/")

        # Automatically get the server hostname
        self.server_name = socket.gethostname()

        self.client = boto3.client(
            "s3",
            endpoint_url=s3_config["endpoint_url"],
            aws_access_key_id=s3_config["access_key"],
            aws_secret_access_key=s3_config["secret_key"],
            region_name=s3_config.get("region", "us-east-1"),
        )

    def check_bucket(self) -> None:
        """Check that the configured S3 bucket exists and is accessible."""
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

        # Example:
        # postgres/db-server-01/2026-08-16/test.dump
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