import logging
import os

def setup_logging():
    # Log-Level aus Umgebungsvariable (default INFO)
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    # Console-Handler
    logging.basicConfig(
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        level=getattr(logging, log_level, logging.INFO),
        handlers=[logging.StreamHandler()]  # stdout
    )
    # File-Handler
    fh = logging.FileHandler("bot.log", encoding="utf-8")
    fh.setLevel(getattr(logging, log_level, logging.INFO))
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    ))
    logging.getLogger().addHandler(fh)