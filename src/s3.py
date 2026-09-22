import logging
from pathlib import Path
from typing import Optional

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import S3TargetConfig


logger = logging.getLogger(__name__)


class S3Client:
    def __init__(
        self,
        target: S3TargetConfig,
        server_name: str,
        prefix_override: Optional[str] = None,
    ):
        self.target_name = target.name
        self.bucket = target.bucket
        self.endpoint_url = target.endpoint_url
        self.prefix = (
            prefix_override
            if prefix_override is not None
            else target.prefix
        )
        self.server_name = server_name

        # Retry configuration
        retry_config = Config(
            retries={
                "max_attempts": 10,
                "mode": "adaptive",
            }
        )

        client_kwargs = {
            "region_name": target.region,
            "config": retry_config,
        }

        if target.endpoint_url:
            client_kwargs["endpoint_url"] = target.endpoint_url

        if target.access_key_id and target.secret_access_key:
            client_kwargs["aws_access_key_id"] = target.access_key_id
            client_kwargs["aws_secret_access_key"] = (
                target.secret_access_key
            )

        if target.profile:
            session = boto3.Session(profile_name=target.profile)
            self.client = session.client("s3", **client_kwargs)
        else:
            self.client = boto3.client("s3", **client_kwargs)

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
            "S3 client initialized target=%s bucket=%s",
            self.target_name,
            self.bucket,
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
                f"'{self.bucket}' for target '{self.target_name}': {exc}"
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
            "Uploading %s to target=%s (s3://%s/%s)",
            file_path,
            self.target_name,
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
                "Upload failed target=%s file=%s",
                self.target_name,
                file_path,
            )

            raise

        logger.info(
            "Upload successful target=%s file=%s",
            self.target_name,
            file_path,
        )

        return object_key

    def delete_object(
        self,
        object_key: str,
    ) -> None:

        logger.info(
            "Deleting object target=%s (s3://%s/%s)",
            self.target_name,
            self.bucket,
            object_key,
        )

        self.client.delete_object(
            Bucket=self.bucket,
            Key=object_key,
        )

    def get_server_prefix(self) -> str:
        """
        Prefix under which all of this server's objects live for this
        target, e.g. "<prefix>/<server_name>/". Used to scope retention
        cleanup so it never touches another server's objects.
        """

        parts = []

        if self.prefix:
            parts.append(
                self.prefix.strip("/")
            )

        parts.append(
            self.server_name.strip("/")
        )

        return "/".join(parts) + "/"

    def list_date_prefixes(self) -> dict[str, list[str]]:
        """
        List every object under this target's server prefix, grouped by
        the immediate next path segment (expected to be a
        "YYYY-MM-DD" date folder). Handles pagination transparently.
        """

        server_prefix = self.get_server_prefix()

        grouped: dict[str, list[str]] = {}

        paginator = self.client.get_paginator(
            "list_objects_v2"
        )

        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=server_prefix,
        ):

            for obj in page.get(
                "Contents",
                [],
            ):

                key = obj["Key"]
                remainder = key[len(server_prefix):]
                segment = remainder.split("/", 1)[0]

                if not segment:
                    continue

                grouped.setdefault(
                    segment, []
                ).append(key)

        return grouped

    def delete_objects_batch(
        self,
        object_keys: list[str],
    ) -> int:
        """
        Delete the given object keys using DeleteObjects, in batches of
        up to 1000 (the API limit). Returns the number of objects
        successfully deleted.
        """

        deleted = 0

        for start in range(0, len(object_keys), 1000):
            batch = object_keys[start:start + 1000]

            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [
                        {"Key": key} for key in batch
                    ],
                    "Quiet": True,
                },
            )

            errors = response.get("Errors", [])

            if errors:
                logger.error(
                    "Failed to delete %d object(s) target=%s: %s",
                    len(errors),
                    self.target_name,
                    errors,
                )

            deleted += len(batch) - len(errors)

        return deleted
