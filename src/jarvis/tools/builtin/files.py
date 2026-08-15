"""Dateizugriff in einem Sandkasten.

Der Sandkasten ist die einzige Sicherung, die hier zählt, deshalb ist sie
strikt: jeder Pfad wird aufgelöst — inklusive Symlinks — und muss danach
innerhalb der Wurzel liegen. Absolute Pfade, `..` und Verweise nach außen
scheitern damit an derselben einen Prüfung, statt an drei Sonderfällen,
von denen man einen vergisst.

Wurzel und Größengrenze kommen beim Bau aus `config/policies.yaml`, nicht
erst beim Aufruf: es ist feste Konfiguration, und so können `risk_for` und
`summarize` sie ebenfalls nutzen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from jarvis.core.errors import PolicyViolation
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.result import ToolResult

MAX_LISTED_ENTRIES = 200
DEFAULT_MAX_BYTES = 512 * 1024


class _SandboxTool(Tool):
    """Gemeinsame Basis: kennt Wurzel und Größengrenze."""

    def __init__(self, root: Path | str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._root = Path(root).expanduser()
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        return self._root

    def _base(self) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root.resolve()

    def _resolve(self, relative: str) -> Path:
        """Zielpfad auflösen und beweisen, dass er im Sandkasten liegt."""
        if not relative or not relative.strip():
            raise PolicyViolation("Es wurde kein Dateiname angegeben.")
        base = self._base()
        target = (base / relative.strip()).resolve()
        if target != base and not target.is_relative_to(base):
            raise PolicyViolation(
                f"'{relative}' liegt außerhalb des erlaubten Verzeichnisses {base}."
            )
        return target

    def _name(self, target: Path) -> str:
        base = self._root.expanduser().resolve()
        return str(target.relative_to(base)) if target != base else "."


class ListFilesTool(_SandboxTool):
    name = "dateien.auflisten"
    description = (
        "Listet Dateien und Ordner im Arbeitsverzeichnis auf. Benutzen, bevor du eine "
        "Datei liest oder schreibst, wenn der genaue Name unklar ist."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "ordner": {
                "type": "string",
                "description": "Unterordner relativ zum Arbeitsverzeichnis. Leer für die Wurzel.",
            }
        },
    }
    risk = RiskLevel.READ

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        folder = str(args.get("ordner") or ".")
        target = self._resolve(folder)
        if not target.is_dir():
            return ToolResult.failure(f"'{folder}' ist kein Ordner.")

        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        listed: list[dict[str, Any]] = [
            {
                "name": entry.name,
                "typ": "ordner" if entry.is_dir() else "datei",
                "bytes": entry.stat().st_size if entry.is_file() else None,
            }
            for entry in entries[:MAX_LISTED_ENTRIES]
        ]
        if not listed:
            return ToolResult.success("Der Ordner ist leer.", data={"eintraege": []})
        names = ", ".join(str(entry["name"]) for entry in listed)
        return ToolResult.success(
            f"{len(listed)} Einträge: {names}",
            data={"eintraege": listed, "abgeschnitten": len(entries) > MAX_LISTED_ENTRIES},
        )


class ReadFileTool(_SandboxTool):
    name = "dateien.lesen"
    description = "Liest eine Textdatei aus dem Arbeitsverzeichnis."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pfad": {"type": "string", "description": "Dateiname relativ zum Arbeitsverzeichnis."}
        },
        "required": ["pfad"],
    }
    risk = RiskLevel.READ

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        relative = str(args["pfad"])
        target = self._resolve(relative)
        if not target.is_file():
            return ToolResult.failure(f"'{relative}' gibt es nicht.")

        size = target.stat().st_size
        if size > self._max_bytes:
            return ToolResult.failure(
                f"Die Datei ist {size // 1024} KB groß, erlaubt sind {self._max_bytes // 1024} KB."
            )
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure(f"'{relative}' ist keine Textdatei.")

        # Dateiinhalt ist eine fremde Quelle: er kann Anweisungen enthalten,
        # die nicht vom Nutzer stammen (Architektur §7).
        return ToolResult.success(
            text,
            data={"pfad": self._name(target), "inhalt": text},
            untrusted=True,
        )


class WriteFileTool(_SandboxTool):
    name = "dateien.schreiben"
    description = (
        "Schreibt Text in eine Datei im Arbeitsverzeichnis. Legt sie an, wenn es sie "
        "noch nicht gibt, und überschreibt sie sonst vollständig."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pfad": {"type": "string", "description": "Dateiname relativ zum Arbeitsverzeichnis."},
            "inhalt": {"type": "string", "description": "Der vollständige neue Inhalt."},
        },
        "required": ["pfad", "inhalt"],
    }
    # Boden: eine neue Datei anzulegen ist harmlos und umkehrbar.
    # Überschreiben ist es nicht — das hebt `risk_for` an.
    risk = RiskLevel.LOW

    def _exists(self, args: dict[str, Any]) -> bool:
        try:
            return self._resolve(str(args.get("pfad", ""))).exists()
        except (PolicyViolation, OSError):
            # Unklar heißt vorsichtig: dann lieber nachfragen.
            return True

    def risk_for(self, args: dict[str, Any]) -> RiskLevel:
        return RiskLevel.SENSITIVE if self._exists(args) else RiskLevel.LOW

    def summarize(self, args: dict[str, Any]) -> str:
        pfad = args.get("pfad", "?")
        chars = len(str(args.get("inhalt", "")))
        verb = "überschreiben" if self._exists(args) else "neu anlegen"
        return f"Soll ich „{pfad}“ mit {chars} Zeichen {verb}?"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target = self._resolve(str(args["pfad"]))
        content = str(args.get("inhalt", ""))
        if len(content.encode("utf-8")) > self._max_bytes:
            return ToolResult.failure(f"Der Inhalt überschreitet {self._max_bytes // 1024} KB.")
        if target.is_dir():
            return ToolResult.failure(f"'{args['pfad']}' ist ein Ordner.")

        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        name = self._name(target)
        return ToolResult.success(
            f"„{name}“ {'überschrieben' if existed else 'angelegt'}.",
            data={"pfad": name, "bytes": len(content.encode("utf-8")), "ueberschrieben": existed},
        )


class DeleteFileTool(_SandboxTool):
    name = "dateien.loeschen"
    description = "Löscht eine Datei im Arbeitsverzeichnis. Endgültig."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pfad": {"type": "string", "description": "Dateiname relativ zum Arbeitsverzeichnis."}
        },
        "required": ["pfad"],
    }
    risk = RiskLevel.DESTRUCTIVE

    def summarize(self, args: dict[str, Any]) -> str:
        return f"Soll ich die Datei „{args.get('pfad', '?')}“ endgültig löschen?"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target = self._resolve(str(args["pfad"]))
        if target == self._base():
            return ToolResult.failure("Das Arbeitsverzeichnis selbst wird nicht gelöscht.")
        if not target.is_file():
            return ToolResult.failure(f"'{args['pfad']}' gibt es nicht.")
        name = self._name(target)
        target.unlink()
        return ToolResult.success(f"„{name}“ gelöscht.", data={"pfad": name})
