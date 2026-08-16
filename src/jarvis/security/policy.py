"""Policy-Engine — `config/policies.yaml` in Entscheidungen übersetzt.

Drei Regeln, die im Code verankert sind und nicht per Konfiguration
umgangen werden können:

1. **Stufen lassen sich nur anheben.** Ein Werkzeug deklariert im Code
   seinen Boden; die Policy kann strenger sein, nie milder. Wäre das
   Senken erlaubt, könnte eine unbedacht editierte YAML-Datei die
   Bestätigungspflicht für Löschaktionen abschalten.
2. **Der Browser hat eine Domain-Allowlist**, und eine leere Liste heißt
   *nichts erlaubt*, nicht *alles erlaubt*. Ein Fehlgriff in der Konfi-
   guration soll das System einschränken, nicht öffnen.
3. **Inhalte können keine Stufe ändern.** Hier kommt nur die Datei an,
   nie etwas, das aus einer Webseite oder einer Mail stammt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog
import yaml
from pydantic import BaseModel, Field

from jarvis.core.errors import PolicyViolation
from jarvis.security.risk import RiskLevel, parse_risk

log = structlog.get_logger(__name__)

DEFAULT_POLICY_PATH = Path("config/policies.yaml")

# Schemata und Zugriffe, die ein Browser-Agent nie anfassen soll — auch
# dann nicht, wenn jemand "*" in die Allowlist schreibt. `file:` läse das
# Dateisystem des Hosts, die übrigen sind die üblichen Wege, an
# Metadaten-Dienste oder das Loopback-Interface zu kommen.
FORBIDDEN_SCHEMES = frozenset({"file", "data", "javascript", "about", "chrome", "view-source"})
FORBIDDEN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "169.254.169.254"})


class ToolPolicy(BaseModel):
    risk: str | None = None
    disabled: bool = False


class BrowserPolicy(BaseModel):
    # Leer = der Browser darf nichts. Bewusst: siehe Modulkopf.
    allowed_domains: list[str] = Field(default_factory=list)
    # Downloads landen ausschließlich hier und werden nicht ausgeführt.
    download_dir: str = "~/JARVIS-Downloads"


class FilesPolicy(BaseModel):
    root: str = "~/JARVIS-Dateien"
    max_file_kb: int = 512


class PolicyDefaults(BaseModel):
    confirm_from: str = "sensitive"
    confirm_timeout_s: float = 60.0
    # Ab dieser Stufe genügt eine gesprochene Zustimmung nicht mehr; es
    # braucht den Tap im Client (Architektur §12).
    require_tap_from: str = "destructive"
    max_tool_steps: int = 6


class Policies(BaseModel):
    """Der geparste Inhalt von `config/policies.yaml`."""

    defaults: PolicyDefaults = Field(default_factory=PolicyDefaults)
    tools: dict[str, ToolPolicy] = Field(default_factory=dict)
    browser: BrowserPolicy = Field(default_factory=BrowserPolicy)
    files: FilesPolicy = Field(default_factory=FilesPolicy)


def _is_private_address(host: str) -> bool:
    """True für Loopback, privates Netz und Link-Local.

    Greift auch dann, wenn in der Allowlist ein `*` steht: eine
    Wildcard-Freigabe ist eine Entscheidung über das *Internet*, nicht
    darüber, dass der Agent den Router oder die NAS im eigenen Netz
    aufrufen darf.
    """
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def load_policies(path: Path | str | None = None) -> Policies:
    policy_path = Path(path or os.getenv("JARVIS_POLICIES") or DEFAULT_POLICY_PATH)
    if not policy_path.is_file():
        log.warning("policies.missing", path=str(policy_path), note="Defaults gelten")
        return Policies()
    raw: Any = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{policy_path}: erwartet wird ein YAML-Mapping auf oberster Ebene")
    return Policies.model_validate(raw)


@dataclass(slots=True)
class PolicyEngine:
    policies: Policies = field(default_factory=Policies)

    # --- Werkzeuge -----------------------------------------------------------

    def is_enabled(self, tool: str) -> bool:
        entry = self.policies.tools.get(tool)
        return not (entry and entry.disabled)

    def effective_risk(self, tool: str, declared: RiskLevel) -> RiskLevel:
        """Der strengere der beiden Werte — nie der mildere."""
        entry = self.policies.tools.get(tool)
        if entry is None or entry.risk is None:
            return declared
        configured = parse_risk(entry.risk)
        if configured < declared:
            log.warning(
                "policy.risk_downgrade_ignored",
                tool=tool,
                declared=str(declared),
                configured=str(configured),
            )
        return max(declared, configured)

    # --- Bestätigung ---------------------------------------------------------

    @property
    def confirm_from(self) -> RiskLevel:
        return parse_risk(self.policies.defaults.confirm_from)

    @property
    def require_tap_from(self) -> RiskLevel:
        return parse_risk(self.policies.defaults.require_tap_from)

    @property
    def confirm_timeout_s(self) -> float:
        return self.policies.defaults.confirm_timeout_s

    @property
    def max_tool_steps(self) -> int:
        return max(1, self.policies.defaults.max_tool_steps)

    def needs_confirmation(self, risk: RiskLevel) -> bool:
        return risk >= self.confirm_from

    def needs_tap(self, risk: RiskLevel) -> bool:
        return risk >= self.require_tap_from

    # --- Browser -------------------------------------------------------------

    @property
    def allowed_domains(self) -> list[str]:
        return list(self.policies.browser.allowed_domains)

    @property
    def download_dir(self) -> Path:
        return Path(self.policies.browser.download_dir).expanduser()

    def check_url(self, url: str) -> str:
        """URL prüfen und normalisiert zurückgeben. Wirft `PolicyViolation`."""
        # Am Schema erkennen, nicht am "://": `javascript:alert(1)` hat
        # keines und würde sonst zu `https://javascript:alert(1)` ergänzt —
        # das parst als Host "javascript" und käme durch die Prüfung.
        parsed = urlparse(url.strip())
        if not parsed.scheme:
            parsed = urlparse(f"https://{url.strip()}")
        scheme = parsed.scheme.lower()
        if scheme in FORBIDDEN_SCHEMES:
            raise PolicyViolation(f"Das Schema '{scheme}:' ist gesperrt.")
        if scheme not in ("http", "https"):
            raise PolicyViolation(f"Nur http und https sind erlaubt, nicht '{scheme}:'.")
        host = (parsed.hostname or "").lower()
        if not host:
            raise PolicyViolation(f"'{url}' enthält keinen Hostnamen.")
        if host in FORBIDDEN_HOSTS or _is_private_address(host):
            raise PolicyViolation(
                f"'{host}' liegt im eigenen Netz und ist für den Browser gesperrt."
            )
        if not self._domain_allowed(host):
            raise PolicyViolation(
                f"'{host}' steht nicht in browser.allowed_domains in config/policies.yaml. "
                "Trage die Domain dort ein, wenn du sie freigeben willst."
            )
        return parsed.geturl()

    def _domain_allowed(self, host: str) -> bool:
        for entry in self.policies.browser.allowed_domains:
            raw = entry.strip().lower()
            if not raw:
                continue
            if raw == "*":
                return True
            # Ein Eintrag deckt die Domain und ihre Unterdomains ab. Das ist
            # das, was Leute mit einer Allowlist meinen; die Glob-Feinheit
            # zwischen `example.com` und `*.example.com` wäre nur eine
            # Fehlerquelle.
            pattern = raw.removeprefix("*.")
            if pattern and (host == pattern or host.endswith(f".{pattern}")):
                return True
        return False

    # --- Dateien -------------------------------------------------------------

    @property
    def files_root(self) -> Path:
        return Path(self.policies.files.root).expanduser()

    @property
    def max_file_bytes(self) -> int:
        return max(1, self.policies.files.max_file_kb) * 1024
