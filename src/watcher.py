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

            observers.append(observer)

            logger.info(
                "Watching: %s",
                source_dir,
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

        for observer in observers:
            observer.join()