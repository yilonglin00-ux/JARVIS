"""Importreihenfolge.

Diese Prüfung existiert wegen eines echten Fehlers: `jarvis/core/__init__`
importierte den Kernel, der Kernel die Werkzeuge, die Werkzeuge die
Sicherheit und die wieder `jarvis.core.events` — ein Zyklus, der sich nur
zeigte, wenn man mit `from jarvis import factory` einstieg. Die Testsuite
importiert Submodule direkt und lief daran vorbei; erst ein Trockenlauf
brachte ihn ans Licht.

Deshalb startet jeder Fall einen eigenen Interpreter: mit einem bereits
gefüllten Modul-Cache ist die Reihenfolge nicht mehr prüfbar.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# Jeder dieser Einstiege muss für sich allein funktionieren.
ENTRY_POINTS = [
    "from jarvis import factory",
    "from jarvis.security.policy import PolicyEngine",
    "from jarvis.security.confirm import ConfirmationBroker",
    "from jarvis.core.events import EventBus",
    "from jarvis.core.kernel import JarvisCore",
    "from jarvis.tools.base import Tool",
    "from jarvis.tools.registry import ToolRegistry",
    "from jarvis.browser.tools import browser_tools",
    "from jarvis.interfaces.server import create_app",
    "import jarvis.core",
    "import jarvis.security",
    "import jarvis.tools",
]


@pytest.mark.parametrize("statement", ENTRY_POINTS)
def test_einstieg_ohne_importzyklus(statement: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", statement],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"{statement}\n{result.stderr}"
