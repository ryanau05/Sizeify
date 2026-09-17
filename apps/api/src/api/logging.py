"""Structured JSON logging.

One line of JSON per event, so the log is greppable by field rather than by
regex once it lands in a log aggregator. ``configure_logging`` is called once
from ``api.main.create_app``; everything else uses a module logger::

    logger = logging.getLogger(__name__)
    logger.warning("refresh_token.replayed", extra={"user_id": str(user_id)})

The message is a stable, dot-separated **event name**, not a sentence. That is
what makes an alert possible: "page me when ``refresh_token.replayed`` exceeds
N per hour" is a rule you can write, while a prose message that someone later
rewords is not.

What must never be logged
-------------------------
PRD §11 is a data-minimization principle, and a log is data collection like
any other. Log the ``user_id`` and nothing else that identifies a person: no
email addresses, no passwords, no tokens, and no token hashes. A token hash is
still a token-shaped secret — an attacker who can grep logs for it can
correlate sessions even without being able to reverse it.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

#: Attributes ``logging`` puts on every record. Anything outside this set was
#: passed by the caller via ``extra=`` and belongs in the JSON payload.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        # Caller-supplied ``extra=`` fields, flattened alongside the rest.
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int | str = logging.INFO) -> None:
    """Install the JSON formatter on the root logger. Idempotent.

    Replaces any existing handlers rather than adding to them, so calling
    ``create_app`` twice (which the test suite does, once per test) does not
    stack duplicate handlers and emit every line N times.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
