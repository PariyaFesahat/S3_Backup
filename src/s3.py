import logging
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self, config: dict):
        s3_config = config["s3"]
        server_config = config["server"]

        self.bucket = s3_config["bucket"]
        self.prefix = s3_config.get("prefix", "")
        self.server_name = server_config["name"]

        # Retry configuration
        retry_config = Config(
            retries={
                "max_attempts": 10,
                "mode": "adaptive",
            }
        )

        self.client = boto3.client(
            "s3",
            endpoint_url=s3_config["endpoint_url"],
            aws_access_key_id=s3_config["access_key"],
            aws_secret_access_key=s3_config["secret_key"],
            region_name=s3_config.get(
                "region",
                "us-east-1",
            ),
            config=retry_config,
        )

        # Multipart upload configuration
        #
        # Lower concurrency is intentional.
        # It reduces the number of simultaneous
        # UploadPart requests sent to MinIO.
        self.transfer_config = TransferConfig(
            multipart_threshold=64 * 1024 * 1024,  # 64 MB
            multipart_chunksize=64 * 1024 * 1024,  # 64 MB
            max_concurrency=2,
            use_threads=True,
        )

        logger.info(
            "S3 client initialized for server: %s",
            self.server_name,
        )

        logger.info(
            "S3 retry configuration: max_attempts=10, mode=adaptive"
        )

        logger.info(
            "S3 multipart configuration: "
            "threshold=64MB, chunk_size=64MB, concurrency=2"
        )

    def check_bucket(self) -> None:
        try:
            self.client.head_bucket(
                Bucket=self.bucket
            )

        except ClientError as exc:
            raise RuntimeError(
                f"Cannot access S3 bucket "
                f"'{self.bucket}': {exc}"
            ) from exc

    def get_backup_prefix(
        self,
        backup_date: str,
        backup_name: str,
    ) -> str:

        parts = []

        if self.prefix:
            parts.append(
                self.prefix.strip("/")
            )

        parts.append(
            self.server_name.strip("/")
        )

        parts.append(
            backup_date
        )

        parts.append(
            backup_name
        )

        return "/".join(parts) + "/"

    def list_files(
        self,
        prefix: str,
    ) -> dict:

        files = {}

        paginator = self.client.get_paginator(
            "list_objects_v2"
        )

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=prefix,
        ):

            for obj in page.get(
                "Contents",
                [],
            ):

                key = obj["Key"]

                if key.endswith("/"):
                    continue

                relative_path = key[
                    len(prefix):
                ]

                files[relative_path] = {
                    "key": key,
                    "size": obj["Size"],
                }

        return files

    def upload_file(
        self,
        file_path: Path,
        object_key: str,
    ) -> str:

        file_path = file_path.resolve()

        logger.info(
            "Uploading: %s -> s3://%s/%s",
            file_path,
            self.bucket,
            object_key,
        )

        try:

            self.client.upload_file(
                str(file_path),
                self.bucket,
                object_key,
                Config=self.transfer_config,
            )

        except Exception:

            logger.exception(
                "Upload failed: %s",
                file_path,
            )

            raise

        logger.info(
            "Upload completed: %s",
            file_path,
        )

        return object_key

    def delete_object(
        self,
        object_key: str,
    ) -> None:

        logger.info(
            "Deleting S3 object: s3://%s/%s",
            self.bucket,
            object_key,
        )

        self.client.delete_object(
            Bucket=self.bucket,
            Key=object_key,
        )