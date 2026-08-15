"""Risikostufen (Architektur §12).

Die Reihenfolge ist der ganze Sinn der Sache: weil `RiskLevel` eine
`IntEnum` ist, lässt sich „mindestens so gefährlich wie“ als `>=`
schreiben, und das Anheben einer Stufe durch die Policy ist schlicht ein
`max()`. Ein Absenken gibt es nicht — es ist nirgends implementiert, und
das ist Absicht, nicht Vergesslichkeit.
"""

from __future__ import annotations

from enum import IntEnum


class RiskLevel(IntEnum):
    READ = 0
    """Nur lesen: Wetter abfragen, Seite lesen, Notiz anzeigen."""

    LOW = 1
    """Kleiner, umkehrbarer Schreibzugriff: Notiz anlegen, Datei im Sandkasten."""

    SENSITIVE = 2
    """Wirkt nach außen oder ist schwer rückgängig: Mail senden, Formular abschicken."""

    DESTRUCTIVE = 3
    """Zerstörend oder kostenpflichtig: löschen, zahlen, force-push."""

    @property
    def label(self) -> str:
        """Kurzbeschreibung für die Rückfrage im Client."""
        return _LABELS[self]

    def __str__(self) -> str:
        return self.name.lower()


_LABELS: dict[RiskLevel, str] = {
    RiskLevel.READ: "liest nur",
    RiskLevel.LOW: "ändert wenig und ist umkehrbar",
    RiskLevel.SENSITIVE: "wirkt nach außen",
    RiskLevel.DESTRUCTIVE: "ist zerstörend oder kostet Geld",
}


def parse_risk(value: str | int | RiskLevel) -> RiskLevel:
    """Aus YAML kommt „sensitive“, aus Code kommt `RiskLevel.SENSITIVE`."""
    if isinstance(value, RiskLevel):
        return value
    if isinstance(value, int):
        return RiskLevel(value)
    try:
        return RiskLevel[value.strip().upper()]
    except KeyError as exc:
        allowed = ", ".join(level.name.lower() for level in RiskLevel)
        raise ValueError(f"Unbekannte Risikostufe '{value}'. Erlaubt: {allowed}") from exc
