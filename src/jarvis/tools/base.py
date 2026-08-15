"""Die Werkzeug-Abstraktion.

Ein Werkzeug beschreibt sich selbst (Name, Beschreibung, JSON-Schema),
deklariert seinen **Risikoboden** und führt aus. Was es ausdrücklich
*nicht* tut: entscheiden, ob es eine Bestätigung braucht. Das macht die
Registry für alle Werkzeuge gleich — ein Werkzeugautor kann die Rückfrage
also nicht vergessen und auch nicht bewusst überspringen.

`description` ist kein Kommentar, sondern der Auslöser: daran entscheidet
das Modell, wann es dieses Werkzeug wählt. Ungenaue Beschreibungen sind
die häufigste Ursache für falsch gewählte Werkzeuge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from jarvis.core.events import EventBus
from jarvis.security.confirm import ConfirmationBroker
from jarvis.security.policy import PolicyEngine
from jarvis.security.risk import RiskLevel
from jarvis.tools.result import ToolResult

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass(slots=True)
class ToolContext:
    """Was ein Werkzeug über seine Umgebung wissen darf.

    Bewusst schmal: kein Zugriff auf die Session, keinen LLM-Client, keine
    anderen Werkzeuge. Ein Werkzeug, das den Verlauf lesen könnte, wäre
    nicht mehr für sich testbar.
    """

    session_id: str
    bus: EventBus
    policy: PolicyEngine
    confirm: ConfirmationBroker
    # Zwischenablage für die Dauer einer Aufgabe. Ab Phase 6 tritt das
    # Working Memory an diese Stelle; bis dahin genügt ein Dict.
    scratch: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    name: str = ""
    description: str = ""
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    risk: RiskLevel = RiskLevel.READ
    timeout_s: float = 30.0

    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    def risk_for(self, args: dict[str, Any]) -> RiskLevel:
        """Verschärfung abhängig von den Argumenten.

        Eine neue Datei anzulegen ist harmlos, eine bestehende zu
        überschreiben nicht — dieselbe Signatur, zwei Risiken. Wie bei der
        Policy gilt: nur anheben. Die Registry nimmt ohnehin das Maximum
        aus deklariertem Boden, dieser Methode und der Policy.
        """
        return self.risk

    def summarize(self, args: dict[str, Any]) -> str:
        """Die Klartextfrage vor einer bestätigungspflichtigen Aktion.

        Werkzeuge ab `SENSITIVE` sollten das überschreiben. „Soll ich die
        Mail an Anna mit dem Betreff ‚Angebot' senden?" ist eine Frage, die
        man beantworten kann; „Tool ausführen?“ ist keine.
        """
        if not args:
            return f"„{self.name}“ ausführen?"
        pairs = ", ".join(f"{key}={value!r}" for key, value in args.items())
        return f"„{self.name}“ ausführen mit {pairs}?"

    def validate(self, args: dict[str, Any]) -> str:
        """Leichte Schemaprüfung. Leerer String heißt: in Ordnung.

        Absichtlich keine vollständige JSON-Schema-Implementierung — das
        Modell hält sich in aller Regel an das Schema, und der wirklich
        teure Fehlerfall ist ein *fehlendes* Pflichtfeld, das im Werkzeug
        dann als `KeyError` explodiert.
        """
        schema = self.input_schema
        properties: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if args.get(key) in (None, ""):
                return f"Pflichtfeld '{key}' fehlt."
        for key, value in args.items():
            declared = properties.get(key, {}).get("type")
            expected = _JSON_TYPES.get(declared) if isinstance(declared, str) else None
            if expected is None or value is None:
                continue
            # bool ist in Python ein int — für "number" wäre True sonst gültig.
            if isinstance(value, bool) and expected is not bool:
                return f"Feld '{key}' erwartet {declared}, bekam einen Wahrheitswert."
            if not isinstance(value, expected):
                return f"Feld '{key}' erwartet {declared}, bekam {type(value).__name__}."
        return ""
