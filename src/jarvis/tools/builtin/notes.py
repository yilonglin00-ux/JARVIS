"""Notizen — anhängen und lesen.

Bewusst getrennt von `dateien.schreiben`: Anhängen kann nichts zerstören
und braucht deshalb keine Rückfrage. Genau dafür gibt es abgestufte
Risiken — sonst wäre entweder jede Notiz eine Rückfrage oder jedes
Überschreiben stillschweigend erlaubt.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.result import ToolResult

DEFAULT_FILENAME = "notizen.md"
MAX_RECENT = 20


class AppendNoteTool(Tool):
    name = "notizen.anhaengen"
    description = (
        "Hängt eine Notiz mit Zeitstempel an die Notizdatei an. Benutzen, wenn der "
        "Nutzer sagt, du sollst dir etwas merken, aufschreiben oder notieren."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Der Notiztext, eine Zeile."}},
        "required": ["text"],
    }
    risk = RiskLevel.LOW

    def __init__(self, root: Path | str, *, filename: str = DEFAULT_FILENAME) -> None:
        self._path = Path(root).expanduser() / filename

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = str(args["text"]).strip()
        if not text:
            return ToolResult.failure("Die Notiz ist leer.")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{stamp}] {text}\n")
        return ToolResult.success("Notiert.", data={"datei": self._path.name, "text": text})


class ReadNotesTool(Tool):
    name = "notizen.lesen"
    description = "Liest die letzten Notizen. Benutzen, wenn nach Notiertem gefragt wird."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "anzahl": {
                "type": "integer",
                "description": f"Wie viele der letzten Notizen, höchstens {MAX_RECENT}.",
            }
        },
    }
    risk = RiskLevel.READ

    def __init__(self, root: Path | str, *, filename: str = DEFAULT_FILENAME) -> None:
        self._path = Path(root).expanduser() / filename

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not self._path.is_file():
            return ToolResult.success("Es gibt noch keine Notizen.", data={"notizen": []})
        raw = args.get("anzahl")
        count = min(int(raw), MAX_RECENT) if isinstance(raw, int) and raw > 0 else MAX_RECENT
        lines = [line.strip() for line in self._path.read_text(encoding="utf-8").splitlines()]
        recent = [line for line in lines if line][-count:]
        if not recent:
            return ToolResult.success("Es gibt noch keine Notizen.", data={"notizen": []})
        # Eigene Notizen sind keine fremde Quelle — sie stammen aus diesem
        # System und wurden vom Nutzer diktiert.
        return ToolResult.success("\n".join(recent), data={"notizen": recent})
