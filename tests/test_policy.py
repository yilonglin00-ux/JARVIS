"""Die Policy-Engine.

Der wichtigste Test hier ist `test_policy_kann_risiko_nicht_senken`: er
prüft eine Zusage, die das ganze Sicherheitsmodell trägt. Fiele sie, wäre
jede Bestätigungspflicht durch eine editierte YAML-Datei abschaltbar.
"""

from __future__ import annotations

import pytest

from jarvis.core.errors import PolicyViolation
from jarvis.security.policy import Policies, PolicyEngine
from jarvis.security.risk import RiskLevel, parse_risk


def engine(**raw: object) -> PolicyEngine:
    return PolicyEngine(Policies.model_validate(raw))


# --- Risikostufen -------------------------------------------------------------


def test_policy_hebt_risiko_an() -> None:
    policy = engine(tools={"dateien.schreiben": {"risk": "destructive"}})
    assert policy.effective_risk("dateien.schreiben", RiskLevel.LOW) is RiskLevel.DESTRUCTIVE


def test_policy_kann_risiko_nicht_senken() -> None:
    """Ein milderer Wert in der Konfiguration wird ignoriert, nicht übernommen."""
    policy = engine(tools={"dateien.loeschen": {"risk": "read"}})
    assert policy.effective_risk("dateien.loeschen", RiskLevel.DESTRUCTIVE) is RiskLevel.DESTRUCTIVE


def test_unbekanntes_werkzeug_behaelt_seinen_boden() -> None:
    assert engine().effective_risk("irgendwas", RiskLevel.SENSITIVE) is RiskLevel.SENSITIVE


def test_abgeschaltetes_werkzeug() -> None:
    policy = engine(tools={"browser.klicken": {"disabled": True}})
    assert not policy.is_enabled("browser.klicken")
    assert policy.is_enabled("zeit.jetzt")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("read", RiskLevel.READ),
        ("SENSITIVE", RiskLevel.SENSITIVE),
        (" destructive ", RiskLevel.DESTRUCTIVE),
        (2, RiskLevel.SENSITIVE),
    ],
)
def test_risiko_parsen(text: str | int, expected: RiskLevel) -> None:
    assert parse_risk(text) is expected


def test_unbekannte_risikostufe_wirft() -> None:
    with pytest.raises(ValueError, match="Unbekannte Risikostufe"):
        parse_risk("ziemlich schlimm")


# --- Bestätigungsschwellen ----------------------------------------------------


def test_schwellen() -> None:
    policy = engine(defaults={"confirm_from": "sensitive", "require_tap_from": "destructive"})
    assert not policy.needs_confirmation(RiskLevel.LOW)
    assert policy.needs_confirmation(RiskLevel.SENSITIVE)
    assert policy.needs_confirmation(RiskLevel.DESTRUCTIVE)
    assert not policy.needs_tap(RiskLevel.SENSITIVE)
    assert policy.needs_tap(RiskLevel.DESTRUCTIVE)


# --- Domain-Allowlist ---------------------------------------------------------


def test_leere_allowlist_erlaubt_nichts() -> None:
    """Eine vergessene Konfiguration muss einschränken, nicht öffnen."""
    with pytest.raises(PolicyViolation, match="allowed_domains"):
        engine().check_url("https://example.com/seite")


def test_allowlist_deckt_unterdomains_ab() -> None:
    policy = engine(browser={"allowed_domains": ["example.com"]})
    assert policy.check_url("https://example.com/a")
    assert policy.check_url("https://de.example.com/b")
    with pytest.raises(PolicyViolation):
        policy.check_url("https://example.com.angreifer.test/c")


def test_wildcard_erlaubt_alles_ausser_dem_eigenen_netz() -> None:
    policy = engine(browser={"allowed_domains": ["*"]})
    assert policy.check_url("https://irgendwo.test/x")
    for intern in ("http://127.0.0.1:8765", "http://192.168.1.1", "http://localhost/admin"):
        with pytest.raises(PolicyViolation, match="eigenen Netz"):
            policy.check_url(intern)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,<h1>x", "ftp://example.com"],
)
def test_gefaehrliche_schemata(url: str) -> None:
    policy = engine(browser={"allowed_domains": ["*"]})
    with pytest.raises(PolicyViolation):
        policy.check_url(url)


def test_url_ohne_schema_wird_https() -> None:
    policy = engine(browser={"allowed_domains": ["example.com"]})
    assert policy.check_url("example.com/preise").startswith("https://")
