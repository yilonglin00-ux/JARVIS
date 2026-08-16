"""Argumente kürzen, bevor sie in Log oder Event landen.

Zwei verschiedene Leser, zwei verschiedene Regeln:

* Der **Nutzer** in der Bestätigungsrückfrage muss die *echten* Werte
  sehen. Eine Rückfrage, die den Empfänger einer Mail verschweigt, ist
  keine Rückfrage. Dort wird nur gekürzt, nicht maskiert.
* Das **Log** bekommt Werte, die nach Geheimnis aussehen, nur als Marke.
"""

from __future__ import annotations

from typing import Any

# Substring-Treffer, nicht exakte Namen: `api_key`, `apiKey`, `auth_token`
# und `password2` sollen alle greifen.
SECRET_HINTS = ("password", "passwort", "secret", "token", "api_key", "apikey", "credential")

MAX_VALUE_CHARS = 300


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}… (+{len(value) - limit} Zeichen)"


def redact_arguments(
    arguments: dict[str, Any],
    *,
    mask_secrets: bool = True,
    limit: int = MAX_VALUE_CHARS,
) -> dict[str, Any]:
    """Argumente für die Anzeige aufbereiten.

    `mask_secrets=False` ist für die Bestätigungsrückfrage gedacht — dort
    zählt Vollständigkeit mehr als Diskretion, weil der Nutzer die Aktion
    beurteilen soll, die gleich passiert.
    """
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if mask_secrets and _looks_secret(key):
            out[key] = "<verborgen>"
        elif isinstance(value, str):
            out[key] = _shorten(value, limit)
        else:
            out[key] = value
    return out
