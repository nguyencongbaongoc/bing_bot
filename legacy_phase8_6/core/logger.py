"""
Logger utility module re-exporting logger from core.tracker and suppressing
production log file writes when running under unit tests.
"""
import logging
import os
import sys

from core.tracker import (
    logger,
    _logger,
    _log_info,
    _log_debug,
    _log_warning,
    _log_error,
    _log_exception,
)


def is_testing_mode() -> bool:
    """Return True when running under unittest/pytest or REWARDS_BOT_TESTING=1."""
    return (
        ("unittest" in sys.modules)
        or ("pytest" in sys.modules)
        or ("PYTEST_CURRENT_TEST" in os.environ)
        or (os.environ.get("REWARDS_BOT_TESTING") == "1")
    )


def configure_test_logging(target_logger: logging.Logger = None) -> None:
    """Remove any FileHandler during test runs so logs/bot.log is never polluted."""
    if not is_testing_mode():
        return
    log_obj = target_logger or _logger
    for handler in list(log_obj.handlers):
        if isinstance(handler, logging.FileHandler):
            log_obj.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    if not log_obj.handlers:
        log_obj.addHandler(logging.NullHandler())


configure_test_logging(_logger)

__all__ = [
    "logger",
    "_logger",
    "_log_info",
    "_log_debug",
    "_log_warning",
    "_log_error",
    "_log_exception",
    "is_testing_mode",
    "configure_test_logging",
]

