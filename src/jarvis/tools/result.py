"""Das Ergebnis eines Werkzeugaufrufs.

Zwei Adressaten, deshalb zwei Felder: `data` geht an das Modell zurück und
darf strukturiert sein, `display_text` ist die Fassung, die JARVIS
aussprechen oder anzeigen kann. Wer beides vermischt, bekommt entweder
vorgelesenes JSON oder ein Modell, das mit Prosa weiterrechnen muss.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Self


@dataclass(frozen=True, slots=True)
class Citation:
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool = True
    data: Any = None
    display_text: str = ""
    error: str = ""
    citations: tuple[Citation, ...] = ()
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    # Inhalte aus dem Netz, aus Mails oder Dateien sind Daten, keine
    # Anweisungen. Ist das gesetzt, umschließt der Kernel das Ergebnis mit
    # einer Warnung an das Modell (Architektur §7).
    untrusted: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        display_text: str,
        *,
        data: Any = None,
        untrusted: bool = False,
        citations: tuple[Citation, ...] = (),
        **meta: Any,
    ) -> Self:
        return cls(
            ok=True,
            data=data if data is not None else display_text,
            display_text=display_text,
            untrusted=untrusted,
            citations=citations,
            meta=meta,
        )

    @classmethod
    def failure(cls, error: str, **meta: Any) -> Self:
        return cls(ok=False, error=error, display_text=error, meta=meta)

    def for_model(self) -> str:
        """Die Fassung, die als Werkzeugergebnis zurück ins Gespräch geht."""
        if not self.ok:
            return f"Fehler: {self.error}"
        payload = self.data if self.data is not None else self.display_text
        if isinstance(payload, str):
            body = payload
        else:
            body = json.dumps(payload, ensure_ascii=False, default=str)
        if self.citations:
            quellen = "\n".join(f"- {c.title}: {c.url}" for c in self.citations)
            body = f"{body}\n\nQuellen:\n{quellen}"
        return body
