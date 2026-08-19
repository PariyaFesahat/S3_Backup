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

        # We need:
        #
        # /dump/<backup-directory>/...
        #
        # Therefore there must be at least
        # two path components.
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

            existing_timer = self.timers.get(
                key
            )

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

            logger.info(
                "Change detected. "
                "Synchronizing: %s",
                backup_dir,
            )

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
            self._get_backup_directory(
                path
            )
        )

        if backup_dir is None:
            return

        logger.debug(
            "Filesystem event detected: %s",
            path,
        )

        self._schedule_sync(
            backup_dir
        )

    def on_created(self, event):

        if event.is_directory:
            self._handle_event(
                event.src_path
            )
            return

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
        source_dirs: list[str],
        manager,
        debounce_seconds: int = 5,
    ):

        self.source_dirs = [
            Path(path).resolve()
            for path in source_dirs
        ]

        self.manager = manager

        self.debounce_seconds = (
            debounce_seconds
        )

    def start(self) -> None:

        observers = []

        for source_dir in self.source_dirs:

            if not source_dir.exists():

                logger.warning(
                    "Watch directory does not exist: %s",
                    source_dir,
                )

                continue

            if not source_dir.is_dir():

                logger.warning(
                    "Watch path is not a directory: %s",
                    source_dir,
                )

                continue

            event_handler = BackupEventHandler(
                source_dir=source_dir,
                manager=self.manager,
                debounce_seconds=(
                    self.debounce_seconds
                ),
            )

            observer = Observer()

            observer.schedule(
                event_handler,
                str(source_dir),
                recursive=True,
            )

            observer.start()

            observers.append(
                observer
            )

            logger.info(
                "Watching: %s",
                source_dir,
            )

        if not observers:

            raise RuntimeError(
                "No valid backup directories "
                "are available for watching."
            )

        logger.info(
            "Debounce: %s seconds",
            self.debounce_seconds,
        )

        try:

            while True:

                for observer in observers:
                    observer.join(1)

        except KeyboardInterrupt:

            logger.info(
                "Stopping watchers..."
            )

            for observer in observers:
                observer.stop()

        finally:

            for observer in observers:
                observer.stop()

            for observer in observers:
                observer.join()

            logger.info(
                "All watchers stopped."
            )