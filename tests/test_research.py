"""Recherche: Entdoppeln, Bewerten, Widersprüche, Zitate.

Der Kern dieser Datei ist die Abnahmebedingung aus Phase 4: **ein
widersprüchliches Thema wird als widersprüchlich benannt.** Geprüft wird
das an zwei Stellen — der Evaluator muss den Konflikt finden, und
`render_evidence` muss ihn so aufschreiben, dass er dem Modell nicht
entgehen kann.
"""

from __future__ import annotations

from datetime import date, timedelta

from jarvis.research.base import Finding, ResearchQuery, Source
from jarvis.research.evaluator import Evaluator, find_numeric_conflicts, normalize_url
from jarvis.research.fake import FakeResearch, finding
from jarvis.research.pipeline import ResearchPipeline, render_evidence

QUERY = ResearchQuery(question="Wie hoch ist der Beitrag?")


# --- Entdoppeln ---------------------------------------------------------------


def test_urls_normalisieren() -> None:
    gleich = [
        "https://example.com/artikel",
        "https://example.com/artikel/",
        "https://www.example.com/artikel",
        "https://Example.com/artikel?utm_source=news",
        "https://example.com/artikel#abschnitt",
    ]
    assert len({normalize_url(url) for url in gleich}) == 1


def test_echte_parameter_bleiben() -> None:
    assert normalize_url("https://example.com/s?q=bremen") != normalize_url("https://example.com/s")


def test_gleiche_seite_von_zwei_anbietern_zaehlt_einmal() -> None:
    """Zwei Anbieter, die dieselbe Seite finden, bestätigen nichts."""
    evidence = Evaluator().evaluate(
        [
            finding("https://example.com/a", "Der Beitrag liegt bei 12 Prozent.", via="perplexity"),
            finding("https://example.com/a?utm_source=x", "12 Prozent laut B.", via="brave"),
        ]
    )
    assert len(evidence.findings) == 1
    assert evidence.duplicates_removed == 1
    # Beide Wege bleiben nachvollziehbar.
    assert evidence.findings[0].source.via == "perplexity+brave"


def test_eine_domain_heisst_nicht_abgeglichen() -> None:
    evidence = Evaluator().evaluate(
        [
            finding("https://example.com/a", "A"),
            finding("https://example.com/b", "B"),
        ]
    )
    assert evidence.is_thin

    breiter = Evaluator().evaluate(
        [finding("https://example.com/a", "A"), finding("https://andere.test/b", "B")]
    )
    assert not breiter.is_thin


# --- Widersprüche -------------------------------------------------------------


def test_widerspruch_zwischen_domains_wird_gefunden() -> None:
    """Die Abnahmebedingung der Phase, auf Datenebene."""
    konflikte = find_numeric_conflicts(
        [
            finding("https://a.test/x", "Der Beitrag steigt auf 12 Prozent.", via="perplexity"),
            finding("https://b.test/y", "Der Beitrag steigt auf 15 Prozent.", via="brave"),
        ]
    )
    assert len(konflikte) == 1
    assert konflikte[0].unit == "%"
    assert dict(konflikte[0].claims) == {"a.test": "12", "b.test": "15"}


def test_gleiche_zahl_ist_kein_widerspruch() -> None:
    assert (
        find_numeric_conflicts(
            [
                finding("https://a.test/x", "Genau 12 Prozent."),
                finding("https://b.test/y", "Es sind 12 %."),
            ]
        )
        == []
    )


def test_schreibweise_ist_kein_widerspruch() -> None:
    """„1.234,5 Euro“ und „1234,5 €“ sind derselbe Wert."""
    assert (
        find_numeric_conflicts(
            [
                finding("https://a.test/x", "Das kostet 1.234,5 Euro."),
                finding("https://b.test/y", "Der Preis: 1234,5 €."),
            ]
        )
        == []
    )


def test_tausendertrenner_ohne_dezimalteil() -> None:
    """„1.234 Euro“ ist eintausendzweihundertvierunddreißig, nicht 1,234."""
    assert (
        find_numeric_conflicts(
            [
                finding("https://a.test/x", "1.234 Euro."),
                finding("https://b.test/y", "1234 Euro."),
            ]
        )
        == []
    )


def test_teilzahl_wird_nicht_aus_laengerer_zahl_gelesen() -> None:
    """Der Fehler, den `test_schreibweise` aufgedeckt hat: 1234,5 wurde 234,5."""
    konflikte = find_numeric_conflicts(
        [
            finding("https://a.test/x", "1234,5 Euro."),
            finding("https://b.test/y", "999 Euro."),
        ]
    )
    assert dict(konflikte[0].claims)["a.test"] == "1234.5"


def test_aufzaehlung_in_einer_quelle_ist_kein_widerspruch() -> None:
    """Eine Seite, die eine Zeitreihe nennt, widerspricht sich nicht selbst."""
    assert (
        find_numeric_conflicts(
            [
                finding("https://a.test/x", "2023 waren es 12 Prozent, 2024 dann 15 Prozent."),
                finding("https://b.test/y", "Ohne Zahlenangabe."),
            ]
        )
        == []
    )


def test_verschiedene_einheiten_werden_nicht_verglichen() -> None:
    assert (
        find_numeric_conflicts(
            [
                finding("https://a.test/x", "12 Prozent."),
                finding("https://b.test/y", "15 Euro."),
            ]
        )
        == []
    )


# --- Aktualität ---------------------------------------------------------------


def test_neuere_quelle_steht_vorn() -> None:
    heute = date(2026, 8, 15)
    alt = Finding(
        source=Source(url="https://a.test/x", published=heute - timedelta(days=900)),
        text="alt",
        relevance=0.5,
    )
    neu = Finding(
        source=Source(url="https://b.test/y", published=heute - timedelta(days=3)),
        text="neu",
        relevance=0.5,
    )
    evidence = Evaluator(today=heute).evaluate([alt, neu])
    assert [f.text for f in evidence.findings] == ["neu", "alt"]


# --- Darstellung für das Modell -----------------------------------------------


def test_belege_bleiben_je_quelle_getrennt() -> None:
    """Ohne Absender könnte das Modell einen Widerspruch nicht benennen."""
    evidence = Evaluator().evaluate(
        [
            finding("https://a.test/x", "12 Prozent.", title="Quelle A"),
            finding("https://b.test/y", "15 Prozent.", title="Quelle B"),
        ]
    )
    text = render_evidence(evidence, question="Wie hoch?")

    assert "Quelle A" in text and "Quelle B" in text
    assert "https://a.test/x" in text and "https://b.test/y" in text
    assert "nicht einig" in text
    # Die Zitatpflicht steht bewusst *nicht* hier drin: dieser Text wird
    # gleich als fremde Quelle eingeklammert.
    assert "Löse den Widerspruch nicht auf" not in text


def test_duenne_beleglage_wird_benannt() -> None:
    evidence = Evaluator().evaluate([finding("https://a.test/x", "Nur eine Quelle.")])
    assert "gegengeprüft" in render_evidence(evidence, question="Wie hoch?")


def test_ausgefallene_anbieter_werden_benannt() -> None:
    evidence = Evaluator().evaluate(
        [finding("https://a.test/x", "A")], failed_providers=("brave: Zeitüberschreitung",)
    )
    assert "Nicht erreichbar: brave" in render_evidence(evidence, question="?")


# --- Pipeline -----------------------------------------------------------------


async def test_provider_laufen_parallel_und_werden_zusammengefuehrt() -> None:
    pipeline = ResearchPipeline(
        providers=[
            FakeResearch([finding("https://a.test/x", "12 Prozent.", via="p")], name="p"),
            FakeResearch([finding("https://b.test/y", "15 Prozent.", via="b")], name="b"),
        ]
    )
    evidence = await pipeline.run(QUERY)

    assert len(evidence.findings) == 2
    assert len(evidence.conflicts) == 1


async def test_ein_ausfall_stoppt_die_recherche_nicht() -> None:
    pipeline = ResearchPipeline(
        providers=[
            FakeResearch([finding("https://a.test/x", "Ein Beleg.")], name="gut"),
            FakeResearch(name="kaputt", error="Zeitüberschreitung"),
        ]
    )
    evidence = await pipeline.run(QUERY)

    assert len(evidence.findings) == 1
    assert evidence.failed_providers == ("kaputt: Zeitüberschreitung",)


async def test_werfender_provider_beendet_den_turn_nicht() -> None:
    """Ein Provider, der sich nicht an das Interface hält, darf nicht durchschlagen."""

    class Kaputt(FakeResearch):
        async def search(self, query: ResearchQuery) -> object:  # type: ignore[override]
            raise RuntimeError("Verbindung verloren")

    pipeline = ResearchPipeline(
        providers=[
            FakeResearch([finding("https://a.test/x", "Ein Beleg.")], name="gut"),
            Kaputt(name="wirft"),
        ]
    )
    evidence = await pipeline.run(QUERY)

    assert len(evidence.findings) == 1
    assert evidence.failed_providers == ("wirft (RuntimeError)",)


async def test_ohne_provider_kein_absturz() -> None:
    evidence = await ResearchPipeline().run(QUERY)
    assert evidence.findings == ()
    assert evidence.failed_providers
