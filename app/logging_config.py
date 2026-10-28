from __future__ import annotations

import sys

from loguru import logger

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <7}</level> | "
    "<cyan>{extra[source]: <11}</cyan> | {extra[run_id]} | {message}"
)


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    logger.remove()
    logger.configure(extra={"source": "app", "run_id": "-"})
    if json_logs:
        logger.add(sys.stderr, level=level, serialize=True)
    else:
        logger.add(sys.stderr, level=level, format=_FORMAT)


def get_logger(source: str, **context):
    """Return a logger bound to a data source / pipeline stage."""
    return logger.bind(source=source, **context)


# Sensible default for library use and tests (overridden by the CLI).
logger.configure(extra={"source": "app", "run_id": "-"})
