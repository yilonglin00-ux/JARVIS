"""Uhrzeit und Datum.

Wirkt trivial und ist es nicht: ohne dieses Werkzeug rät ein Sprachmodell
das Datum aus seinem Trainingsstand und liegt zuverlässig daneben. Es ist
außerdem der billigste Weg, die Werkzeugschleife im Betrieb zu prüfen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.result import ToolResult

WEEKDAYS = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)
MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


class ClockTool(Tool):
    name = "zeit.jetzt"
    description = (
        "Liefert das aktuelle Datum und die aktuelle Uhrzeit. Immer benutzen, wenn "
        "nach Zeit, Datum, Wochentag oder danach gefragt wird, wie spät es ist."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "zeitzone": {
                "type": "string",
                "description": (
                    "IANA-Zeitzone, etwa 'Europe/Berlin'. Weglassen für die Zone des Hosts."
                ),
            }
        },
    }
    risk = RiskLevel.READ

    def __init__(self, *, default_timezone: str = "") -> None:
        self._default_tz = default_timezone

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("zeitzone") or self._default_tz or "").strip()
        tz: ZoneInfo | None = None
        if name:
            try:
                tz = ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                return ToolResult.failure(f"Unbekannte Zeitzone '{name}'.")

        now = datetime.now(tz)
        spoken = (
            f"{WEEKDAYS[now.weekday()]}, {now.day}. {MONTHS[now.month - 1]} {now.year}, "
            f"{now.hour} Uhr {now.minute:02d}"
        )
        return ToolResult.success(
            spoken,
            data={
                "iso": now.isoformat(timespec="seconds"),
                "wochentag": WEEKDAYS[now.weekday()],
                "gesprochen": spoken,
            },
        )
