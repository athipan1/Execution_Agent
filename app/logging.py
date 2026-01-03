import logging
import json
from pythonjsonlogger import jsonlogger

def setup_logging():
    """
    Configures structured JSON logging for the application.
    """
    log = logging.getLogger()
    log.setLevel(logging.INFO)

    # Prevent adding duplicate handlers if this is called multiple times
    if log.hasHandlers():
        log.handlers.clear()

    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    handler.setFormatter(formatter)
    log.addHandler(handler)

def get_logger(name: str):
    """
    Returns a configured logger instance.
    """
    return logging.getLogger(name)

# Call setup_logging() on import.
setup_logging()
