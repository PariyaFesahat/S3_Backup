import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .manager import MultiTargetBackupManager


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

            # One filesystem event drives every target mapped to this
            # path; the manager isolates per-target failures.
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
        path_managers: list[tuple[str, object]],
        debounce_seconds: int = 5,
    ):

        # A source path may fan out to several S3 targets, but it is
        # watched exactly once: managers registered for the same
        # resolved path are merged behind a single event handler, so
        # one filesystem event triggers every target.
        self.path_managers = self._deduplicate(path_managers)

        self.debounce_seconds = (
            debounce_seconds
        )

    @staticmethod
    def _deduplicate(
        path_managers: list[tuple[str, object]],
    ) -> list[tuple[Path, object]]:

        grouped: dict[Path, list[object]] = {}

        for path, manager in path_managers:

            source_dir = Path(path).expanduser().resolve()

            grouped.setdefault(source_dir, []).append(manager)

        merged: list[tuple[Path, object]] = []

        for source_dir, managers in grouped.items():

            if len(managers) == 1:
                merged.append((source_dir, managers[0]))
                continue

            logger.debug(
                "Merging %d managers into a single watcher for %s",
                len(managers),
                source_dir,
            )

            merged.append(
                (source_dir, MultiTargetBackupManager(managers))
            )

        return merged

    def start(self) -> None:

        observers = []

        for source_dir, manager in self.path_managers:

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
                manager=manager,
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

            target_names = getattr(
                manager, "target_names", None
            )

            logger.info(
                "Watching: %s (targets: %s)",
                source_dir,
                ", ".join(target_names)
                if target_names
                else getattr(manager, "target_name", "unknown"),
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
