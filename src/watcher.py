import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


logger = logging.getLogger(__name__)


class BackupEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        source_dir: Path,
        manager,
        debounce_seconds: int = 5,
    ):
        self.source_dir = source_dir.resolve()
        self.manager = manager
        self.debounce_seconds = debounce_seconds

        self.timers = {}
        self.lock = threading.Lock()

    def _get_backup_directory(
        self,
        path: str,
    ) -> Path | None:

        path = Path(path).resolve()

        try:
            relative = path.relative_to(
                self.source_dir
            )
        except ValueError:
            return None

        if len(relative.parts) < 2:
            return None

        backup_name = relative.parts[0]

        backup_dir = (
            self.source_dir / backup_name
        )

        if not backup_dir.is_dir():
            return None

        return backup_dir

    def _schedule_sync(
        self,
        backup_dir: Path,
    ) -> None:

        key = str(
            backup_dir.resolve()
        )

        with self.lock:

            existing_timer = self.timers.get(key)

            if existing_timer is not None:
                existing_timer.cancel()

            timer = threading.Timer(
                self.debounce_seconds,
                self._run_sync,
                args=(backup_dir,),
            )

            timer.daemon = True

            self.timers[key] = timer

            timer.start()

            logger.debug(
                "Scheduled synchronization: %s",
                backup_dir,
            )

    def _run_sync(
        self,
        backup_dir: Path,
    ) -> None:

        key = str(
            backup_dir.resolve()
        )

        try:
            self.manager.sync_directory(
                backup_dir
            )

        except Exception:
            logger.exception(
                "Error synchronizing: %s",
                backup_dir,
            )

        finally:
            with self.lock:
                self.timers.pop(
                    key,
                    None,
                )

    def _handle_event(
        self,
        path: str,
    ) -> None:

        backup_dir = (
            self._get_backup_directory(path)
        )

        if backup_dir is None:
            return

        logger.debug(
            "Filesystem event: %s",
            path,
        )

        self._schedule_sync(
            backup_dir
        )

    def on_created(self, event):
        self._handle_event(
            event.src_path
        )

    def on_modified(self, event):
        self._handle_event(
            event.src_path
        )

    def on_deleted(self, event):
        self._handle_event(
            event.src_path
        )

    def on_moved(self, event):
        self._handle_event(
            event.src_path
        )

        self._handle_event(
            event.dest_path
        )


class BackupWatcher:
    def __init__(
        self,
        source_dir: str,
        manager,
        debounce_seconds: int = 5,
    ):
        self.source_dir = Path(
            source_dir
        ).resolve()

        self.manager = manager

        self.debounce_seconds = (
            debounce_seconds
        )

    def start(self) -> None:

        if not self.source_dir.exists():
            raise FileNotFoundError(
                f"Watch directory does not exist: "
                f"{self.source_dir}"
            )

        event_handler = BackupEventHandler(
            source_dir=self.source_dir,
            manager=self.manager,
            debounce_seconds=(
                self.debounce_seconds
            ),
        )

        observer = Observer()

        observer.schedule(
            event_handler,
            str(self.source_dir),
            recursive=True,
        )

        observer.start()

        logger.info(
            "Watching: %s",
            self.source_dir,
        )

        logger.info(
            "Debounce: %s seconds",
            self.debounce_seconds,
        )

        try:
            while True:
                observer.join(1)

        except KeyboardInterrupt:
            logger.info(
                "Stopping watcher..."
            )

            observer.stop()

        observer.join()