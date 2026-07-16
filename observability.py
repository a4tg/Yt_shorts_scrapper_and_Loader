import json
import logging
import threading
import time
import uuid
from collections import Counter
from typing import Any


logger = logging.getLogger("yt_loader.access")


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self.started_at = time.time()

    def increment(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[key] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)


metrics = Metrics()


def request_id(value: str | None) -> str:
    if value and len(value) <= 80 and all(character.isalnum() or character in "-_." for character in value):
        return value
    return uuid.uuid4().hex


def log_request(**fields: Any) -> None:
    logger.info(json.dumps({"event": "http_request", **fields}, ensure_ascii=False, separators=(",", ":")))


def prometheus_text(extra: dict[str, int] | None = None) -> str:
    values = metrics.snapshot()
    values.update(extra or {})
    values["process_uptime_seconds"] = int(time.time() - metrics.started_at)
    lines = []
    for key, value in sorted(values.items()):
        safe_key = "".join(character if character.isalnum() or character == "_" else "_" for character in key)
        lines.append(f"yt_loader_{safe_key} {int(value)}")
    return "\n".join(lines) + "\n"
