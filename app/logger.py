import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as structured, machine-parsable JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process": record.process,
            "thread": record.threadName,
        }

        for field in ("correlation_id", "cache_status", "cache_key", "duration_ms", "status_code", "path", "method", "lock_key"):
            if hasattr(record, field):
                log_data[field] = getattr(record, field)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logger(name: str = "onchain", level: str = "INFO", json_format: bool = True) -> logging.Logger:
    """Configures and returns a structured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        if json_format:
            handler.setFormatter(StructuredJsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
            )
        logger.addHandler(handler)

    logger.propagate = False
    return logger


logger = setup_logger()
