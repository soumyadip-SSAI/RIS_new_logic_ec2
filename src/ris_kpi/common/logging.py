"""Logging setup for the CLI (library code only calls logging.getLogger)."""
import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level=logging.INFO):
    # Logs contain characters like '—' / '→' / '×' that the Windows console codepage cannot print
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=level, format=LOG_FORMAT)
