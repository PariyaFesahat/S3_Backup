from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class BackupEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        source_dir: Path,
        manager,
    ):
        self.source_dir = source_dir.resolve()
        self.manager = manager

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

        # We only care about things inside:
        #
        # /dump/<backup-directory>/...
        #
        if len(relative.parts) < 2:
            return None

        backup_name = relative.parts[0]

        backup_dir = (
            self.source_dir / backup_name
        )

        if not backup_dir.is_dir():
            return None

        return backup_dir

    def _sync(self, path: str) -> None:

        backup_dir = self._get_backup_directory(
            path
        )

        if backup_dir is None:
            return

        self.manager.sync_directory(
            backup_dir
        )

    def on_created(self, event):
        if event.is_directory:
            self._sync(event.src_path)
        else:
            self._sync(event.src_path)

    def on_modified(self, event):
        self._sync(event.src_path)

    def on_deleted(self, event):
        self._sync(event.src_path)

    def on_moved(self, event):
        self._sync(event.src_path)
        self._sync(event.dest_path)


class BackupWatcher:
    def __init__(
        self,
        source_dir: str,
        manager,
    ):
        self.source_dir = Path(
            source_dir
        ).resolve()

        self.manager = manager

    def start(self) -> None:
        """
        Start watching the backup directory forever.
        """

        if not self.source_dir.exists():
            raise FileNotFoundError(
                f"Watch directory does not exist: "
                f"{self.source_dir}"
            )

        event_handler = BackupEventHandler(
            source_dir=self.source_dir,
            manager=self.manager,
        )

        observer = Observer()

        observer.schedule(
            event_handler,
            str(self.source_dir),
            recursive=True,
        )

        observer.start()

        print(
            f"Watching: {self.source_dir}"
        )

        try:
            while True:
                observer.join(1)

        except KeyboardInterrupt:
            print("Stopping watcher...")

            observer.stop()

        observer.join()