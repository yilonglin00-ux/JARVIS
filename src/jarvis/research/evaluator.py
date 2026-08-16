"""Befunde ordnen, entdoppeln und Widersprüche sichtbar machen.

Zur Ehrlichkeit vorweg: Widersprüche werden hier **nicht erkannt**. Das
ginge nur semantisch, und ein Regelwerk, das so tut, wäre schlimmer als
keins — es würde übersehene Widersprüche als geprüft ausgeben.

Was hier passiert, sind zwei überprüfbare Dinge:

1. **Zahlen, die nicht zusammenpassen, werden markiert.** Wenn zwei
   unabhängige Domains zur selben Einheit verschiedene Werte nennen, ist
   das ein nachprüfbares Signal — kein Sprachverständnis nötig.
2. **Die Quellenstruktur bleibt erhalten.** Nichts wird zu einem Absatz
   verrechnet. Das Modell bekommt „A sagt X, B sagt Y“ und kann den
   Widerspruch benennen; aus einem eingeebneten Text könnte es das nicht.

Das eigentliche Urteil fällt danach das Modell, mit der Auflage aus dem
Synthese-Prompt. Diese Datei sorgt nur dafür, dass es die Information
überhaupt hat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import structlog

from jarvis.research.base import Finding, Source

log = structlog.get_logger(__name__)

# Parameter, die nur der Nachverfolgung dienen und dieselbe Seite
# verschieden aussehen lassen.
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src")

# Der Lookbehind ist nicht kosmetisch: ohne ihn griff die Tausender-
# Alternative bei „1234,5“ erst ab der zweiten Ziffer und las „234,5“.
# Die erste Alternative verlangt deshalb *mindestens einen* Tausender-
# Trenner, die zweite deckt die glatte Schreibweise ab.
_QUANTITY = re.compile(
    r"(?<![\d.,])"
    r"(?P<value>\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>%|Prozent|Euro|EUR|€|\$|Grad|km|kg|Mio\.?|Mrd\.?|Millionen|Milliarden)",
    re.IGNORECASE,
)

# „1.234“ und „1 234“ sind Tausender, „1.234,5“ auch. Ohne diese
# Unterscheidung wäre „1.234“ mal 1234 und mal 1,234.
_THOUSANDS = re.compile(r"^\d{1,3}(?:[.\s]\d{3})+$")

UNIT_ALIASES = {
    "prozent": "%",
    "eur": "€",
    "euro": "€",
    "mio": "millionen",
    "mio.": "millionen",
    "mrd": "milliarden",
    "mrd.": "milliarden",
}


@dataclass(frozen=True, slots=True)
class Conflict:
    """Zwei Quellen, die zur selben Einheit verschiedene Zahlen nennen."""

    unit: str
    claims: tuple[tuple[str, str], ...]  # (Domain, genannter Wert)

    def describe(self) -> str:
        teile = ", ".join(f"{domain}: {wert}" for domain, wert in self.claims)
        return f"Unterschiedliche Angaben ({self.unit}) — {teile}"


@dataclass(frozen=True, slots=True)
class Evidence:
    """Das aufbereitete Material für die Synthese."""

    findings: tuple[Finding, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    duplicates_removed: int = 0
    failed_providers: tuple[str, ...] = ()

    @property
    def domains(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for f in self.findings:
            seen.setdefault(f.source.domain, None)
        return tuple(seen)

    @property
    def is_thin(self) -> bool:
        """Weniger als zwei unabhängige Domains — dann ist nichts abgeglichen."""
        return len(self.domains) < 2

    def sources(self) -> tuple[Source, ...]:
        return tuple(f.source for f in self.findings)


def normalize_url(url: str) -> str:
    """URLs vergleichbar machen, ohne ihre Bedeutung zu ändern."""
    parsed = urlparse(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key.lower().startswith(marker) for marker in TRACKING_PARAMS)
    ]
    path = parsed.path.rstrip("/") or "/"
    host = (parsed.hostname or "").lower().removeprefix("www.")
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    return urlunparse((parsed.scheme.lower(), host + port, path, "", urlencode(query), ""))


@dataclass(slots=True)
class Evaluator:
    """Bewertet und ordnet Befunde. Kein Netzzugriff, keine Modellaufrufe."""

    max_findings: int = 8
    today: date | None = None

    def evaluate(
        self,
        findings: list[Finding],
        *,
        failed_providers: tuple[str, ...] = (),
    ) -> Evidence:
        merged, removed = self._deduplicate(findings)
        ranked = sorted(merged, key=self._score, reverse=True)[: self.max_findings]
        return Evidence(
            findings=tuple(ranked),
            conflicts=tuple(find_numeric_conflicts(ranked)),
            duplicates_removed=removed,
            failed_providers=failed_providers,
        )

    def _deduplicate(self, findings: list[Finding]) -> tuple[list[Finding], int]:
        """Gleiche URL = eine Quelle, auch wenn zwei Anbieter sie fanden.

        Wichtig, weil sonst eine Zahl doppelt zählt, nur weil zwei Suchwege
        auf denselben Artikel zeigen. Zwei Anbieter, die dieselbe Seite
        finden, bestätigen nichts — sie sagen nur, dass die Seite gut
        auffindbar ist.
        """
        best: dict[str, Finding] = {}
        vias: dict[str, list[str]] = {}
        removed = 0

        for item in findings:
            key = normalize_url(item.source.url)
            if not key:
                continue
            vias.setdefault(key, [])
            if item.source.via and item.source.via not in vias[key]:
                vias[key].append(item.source.via)

            existing = best.get(key)
            if existing is None:
                best[key] = item
                continue
            removed += 1
            # Den inhaltsreicheren Befund behalten.
            if len(item.text) > len(existing.text):
                best[key] = item

        out: list[Finding] = []
        for key, item in best.items():
            found_by = vias.get(key) or []
            source = item.source
            if len(found_by) > 1:
                source = Source(
                    url=source.url,
                    title=source.title,
                    published=source.published,
                    via="+".join(found_by),
                )
            out.append(Finding(source=source, text=item.text, relevance=item.relevance))
        return out, removed

    def _score(self, finding: Finding) -> float:
        score = finding.relevance
        # Von mehreren Wegen gefunden: gut auffindbar, also vermutlich
        # relevant. Ausdrücklich kein Beleg für die Richtigkeit.
        if "+" in finding.source.via:
            score += 0.15
        if finding.text:
            score += 0.1
        score += self._recency_bonus(finding.source.published)
        return score

    def _recency_bonus(self, published: date | None) -> float:
        if published is None:
            return 0.0
        reference = self.today or date.today()
        age = reference - published
        if age < timedelta(0):
            return 0.0
        if age <= timedelta(days=30):
            return 0.2
        if age <= timedelta(days=365):
            return 0.1
        return 0.0


def find_numeric_conflicts(findings: list[Finding]) -> list[Conflict]:
    """Gleiche Einheit, verschiedene Werte, verschiedene Domains.

    Die Einschränkung auf verschiedene Domains ist wesentlich: nennt *eine*
    Seite mehrere Zahlen zur selben Einheit, ist das meist eine Aufzählung
    („2023: 12 %, 2024: 15 %“) und kein Widerspruch.
    """
    by_unit: dict[str, dict[str, set[str]]] = {}

    for finding in findings:
        domain = finding.source.domain
        if not domain:
            continue
        for match in _QUANTITY.finditer(finding.text):
            unit = _canonical_unit(match.group("unit"))
            value = _canonical_value(match.group("value"))
            by_unit.setdefault(unit, {}).setdefault(domain, set()).add(value)

    conflicts: list[Conflict] = []
    for unit, per_domain in sorted(by_unit.items()):
        # Je Domain nur einen Wert betrachten, wenn sie eindeutig ist.
        eindeutig = {
            domain: next(iter(values)) for domain, values in per_domain.items() if len(values) == 1
        }
        if len(eindeutig) < 2 or len(set(eindeutig.values())) < 2:
            continue
        conflicts.append(Conflict(unit=unit, claims=tuple(sorted(eindeutig.items()))))
    return conflicts


def _canonical_unit(raw: str) -> str:
    lowered = raw.strip().lower()
    return UNIT_ALIASES.get(lowered, lowered)


def _canonical_value(raw: str) -> str:
    """„1.234,5“ und „1234,5“ sind derselbe Wert, „12“ und „13“ nicht."""
    text = raw.strip()
    if "," in text:
        # Komma ist im Deutschen das Dezimalzeichen; Punkte und Leerzeichen
        # davor sind Tausendertrenner.
        text = text.replace(".", "").replace(" ", "").replace(",", ".")
    elif _THOUSANDS.match(text):
        text = text.replace(".", "").replace(" ", "")
    try:
        number = float(text)
    except ValueError:
        return raw.strip()
    return f"{number:g}"
