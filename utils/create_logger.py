import os
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# Rotating log defaults (overridable via env vars)
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_DEFAULT_BACKUP_COUNT = 3             # keep 3 rotated copies


def create_logger():
    load_dotenv()
    log_file_path = os.getenv("LOG_PATH", ".")
    os.makedirs(log_file_path, exist_ok=True)
    log_file = os.path.abspath(os.path.join(log_file_path, "log_file.txt"))

    logger = logging.getLogger("personal_notion_integration")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    max_bytes = int(os.getenv("LOG_MAX_BYTES", str(_DEFAULT_MAX_BYTES)))
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", str(_DEFAULT_BACKUP_COUNT)))

    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.info("Logger initialized. Writing logs to %s", log_file)
    return logger
