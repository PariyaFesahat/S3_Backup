import logging
import re
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .s3 import S3Client


logger = logging.getLogger(__name__)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_segment(segment: str):
    """
    Parse a "YYYY-MM-DD" path segment into a date, using strict
    validation so malformed prefixes (wrong shape, invalid calendar
    date) are safely ignored rather than crashing retention.
    """

    if not _DATE_PATTERN.match(segment):
        return None

    try:
        return datetime.strptime(segment, "%Y-%m-%d").date()

    except ValueError:
        return None


def run_retention_cleanup(
    s3_client: S3Client,
    retention_days: int,
    server_name: str,
) -> None:
    """
    Delete date-folder prefixes older than the retention window for a
    single S3 target. Scoped entirely to this target's server prefix,
    so other servers' objects are never listed or touched. Any failure
    is logged and swallowed so the caller (and the next scheduled run)
    can retry -- this must never crash the application.
    """

    logger.info("Starting retention cleanup")
    logger.info("Server: %s", server_name)
    logger.info("Retention: %d days", retention_days)

    cutoff_date = (
        datetime.now(TEHRAN_TZ) - timedelta(days=retention_days)
    ).date()

    logger.info("Cutoff date: %s", cutoff_date.isoformat())

    try:
        grouped = s3_client.list_date_prefixes()

    except Exception:
        logger.exception(
            "Retention cleanup failed while listing objects "
            "(target: %s); will retry on next scheduled run",
            s3_client.target_name,
        )
        return

    server_prefix = s3_client.get_server_prefix()

    for segment in sorted(grouped):
        object_keys = grouped[segment]

        parsed_date = _parse_date_segment(segment)

        if parsed_date is None:
            logger.warning(
                "Ignoring malformed date prefix: %s%s/",
                server_prefix,
                segment,
            )
            continue

        if parsed_date >= cutoff_date:
            continue

        expired_prefix = f"{server_prefix}{segment}/"

        logger.info(
            "Deleting expired prefix: %s",
            expired_prefix,
        )

        try:
            deleted_count = s3_client.delete_objects_batch(
                object_keys
            )

        except Exception:
            logger.exception(
                "Failed to delete expired prefix %s (target: %s); "
                "will retry on next scheduled run",
                expired_prefix,
                s3_client.target_name,
            )
            continue

        logger.info(
            "Deleted %d objects",
            deleted_count,
        )

    logger.info("Retention cleanup completed")


class RetentionScheduler:
    """
    Runs retention cleanup once immediately, then every
    `interval_hours` thereafter, on its own background thread. Fully
    independent from the filesystem watcher: it never blocks it, and
    an error here never terminates it.
    """

    def __init__(
        self,
        s3_clients: list[S3Client],
        retention_days: int,
        server_name: str,
        interval_hours: int = 24,
    ):
        self.s3_clients = s3_clients
        self.retention_days = retention_days
        self.server_name = server_name
        self.interval_seconds = interval_hours * 3600

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _run_once(self) -> None:
        for s3_client in self.s3_clients:
            try:
                run_retention_cleanup(
                    s3_client,
                    self.retention_days,
                    self.server_name,
                )

            except Exception:
                logger.exception(
                    "Unexpected error during retention cleanup "
                    "(target: %s); will retry on next scheduled run",
                    s3_client.target_name,
                )

    def _loop(self) -> None:
        self._run_once()

        while not self._stop_event.wait(self.interval_seconds):
            self._run_once()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop,
            name="retention-cleanup",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
