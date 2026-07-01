import os
import sys

from loguru import logger

from config.constants import LOG_DIR
from src.telegram.telegram_error_handler import AsyncTelegramSink

# Windows consoles default to cp1252, which can't encode emoji/bullets (⚠, ●)
# present in résumé text and log messages — that raises UnicodeEncodeError and
# crashes both raw print() calls and the loguru stdout sink. Force UTF-8 with a
# safe fallback so unencodable characters never abort the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

logger.remove()

minimum_log_level = "DEBUG"

# Terminal output without tracebacks
logger.add(sys.stdout, level=minimum_log_level, backtrace=False, diagnose=False)

logger.add(
    AsyncTelegramSink(
        max_retries=4,
        cooldown=600,  # in case of the same error, we wait 10 minutes
    ),
    level="ERROR",
    format="{message}",
    backtrace=True,
    diagnose=True,
)

# Configuration of logging to a file
logger.add(
    os.path.join(LOG_DIR, "app.log"),
    rotation="10 MB",  # Rotate when file reaches 500 MB
    retention="30 days",  # Keep logs for 10 days
    compression="zip",  # Compress rotated logs
    level=minimum_log_level,
    backtrace=True,
    diagnose=True,
)

# Configuration of logging errors to a file
logger.add(
    os.path.join(LOG_DIR, "error.log"),
    rotation="5 MB",  # Rotate when file reaches 100 MB
    retention="30 days",  # Keep error logs longer
    compression="zip",
    level="ERROR",
    backtrace=True,
    diagnose=True,
)
