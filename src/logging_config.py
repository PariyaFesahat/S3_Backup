import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """
    Configure application logging to stdout.
    """

    numeric_level = getattr(
        logging,
        level.upper(),
        logging.INFO,
    )

    logging.basicConfig(
        level=numeric_level,
        stream=sys.stdout,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )