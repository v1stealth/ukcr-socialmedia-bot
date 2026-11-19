import collections
import logging
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

RESET = "\033[0m"
LEVEL_COLOURS = {
    "DEBUG": "\033[90m",
    "INFO": "\033[36m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;41m",
}
DIM = "\033[90m"


class ColourFormatter(logging.Formatter):
    def __init__(self, use_colour: bool) -> None:
        super().__init__()
        self.use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        name = record.name.replace("ukcr.", "")[:14].ljust(14)
        level = record.levelname.ljust(8)
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if self.use_colour:
            colour = LEVEL_COLOURS.get(record.levelname, "")
            return f"{DIM}{stamp}{RESET} {colour}{level}{RESET} {DIM}{name}{RESET} {message}"
        return f"{stamp} {level} {name} {message}"


class ConsoleStats(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.counts: dict = {}
        self.recent = collections.deque(maxlen=20)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.counts[record.levelname] = self.counts.get(record.levelname, 0) + 1
            if record.levelno >= logging.WARNING:
                self.recent.append((datetime.now(timezone.utc), record.levelname, record.name, record.getMessage()))
        except Exception:
            pass


console_stats = ConsoleStats()
_seen: dict = {}


def setup_logging(level: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(getattr(logging, level, logging.INFO))
    stream.setFormatter(ColourFormatter(sys.stdout.isatty()))
    root.addHandler(stream)

    file_handler = RotatingFileHandler(log_dir / "ukcr.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(ColourFormatter(False))
    root.addHandler(file_handler)

    root.addHandler(console_stats)

    for noisy in ("discord.http", "discord.gateway", "discord.client", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.INFO)
    return logging.getLogger("ukcr")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ukcr.{name}")


def log_throttled(logger: logging.Logger, key: str, level: int, message: str, *args, cooldown: int = 3600) -> None:
    now = time.monotonic()
    if now - _seen.get(key, -1e9) < cooldown:
        return
    _seen[key] = now
    logger.log(level, message, *args)


def banner(lines: list) -> str:
    width = max(len(line) for line in lines) + 4
    top = "┌" + "─" * width + "┐"
    bottom = "└" + "─" * width + "┘"
    body = ["│  " + line.ljust(width - 2) + "│" for line in lines]
    return "\n".join([top, *body, bottom])
