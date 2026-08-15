"""Strukturiertes Logging mit Redaction.

Transkripte und Antworten sind der intimste Teil dieses Systems. Sie
gehören standardmäßig nicht ins Log — nur Länge und ein Kürzel, damit
Fehlersuche möglich bleibt, ohne ein Gesprächsprotokoll auf der Platte zu
hinterlassen (Architektur §12).

`JARVIS_LOG_TRANSCRIPTS=true` hebt das bewusst auf. Der Schalter heißt so,
wie er wirkt, und ist nicht der Default.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from typing import Any

import structlog

# Felder, die ohne Debug-Schalter nie im Klartext im Log stehen.
SENSITIVE_FIELDS = frozenset({"text", "transcript", "reply", "spoken", "user_text", "content"})


def _redact(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
    for key in list(event):
        if key not in SENSITIVE_FIELDS:
            continue
        value = event[key]
        if not isinstance(value, str) or not value:
            continue
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        event[key] = f"<{len(value)} Zeichen, #{digest}>"
    return event


def configure_logging(*, level: str = "info", log_transcripts: bool = False) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=numeric)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if not log_transcripts:
        processors.append(_redact)
    processors += [
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
