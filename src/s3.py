import socket

import boto3
from botocore.exceptions import ClientError


class S3Client:
    def __init__(self, config: dict):
        s3_config = config["s3"]

        self.bucket = s3_config["bucket"]
        self.prefix = s3_config.get("prefix", "").strip("/")

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

    def get_backup_prefix(
        self,
        backup_date: str,
        backup_name: str,
    ) -> str:
        """
        Return the S3 prefix for one backup directory.

        Example:
        postgres/server01/2026-08-18/back090087/
        """

        return (
            f"{self.prefix}/"
            f"{self.server_name}/"
            f"{backup_date}/"
            f"{backup_name}/"
        )

    def list_files(self, prefix: str) -> dict[str, dict]:
        """
        List files under an S3 prefix.

        Returns:
            {
                "file.dump": {
                    "key": "...",
                    "size": 1234,
                    "etag": "..."
                }
            }
        """

        result = {}

        paginator = self.client.get_paginator(
            "list_objects_v2"
        )

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=prefix,
        ):
            for obj in page.get("Contents", []):
                key = obj["Key"]

                relative_path = key[len(prefix):]

                result[relative_path] = {
                    "key": key,
                    "size": obj["Size"],
                    "etag": obj.get("ETag", "").strip('"'),
                }

        return result

    def upload_file(
        self,
        file_path,
        object_key: str,
    ) -> None:
        """
        Upload or replace an S3 object.
        """

        self.client.upload_file(
            str(file_path),
            self.bucket,
            object_key,
        )

    def delete_object(self, object_key: str) -> None:
        """
        Delete an S3 object.
        """

        self.client.delete_object(
            Bucket=self.bucket,
            Key=object_key,
        )