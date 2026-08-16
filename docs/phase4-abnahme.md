# Phase 4 — Abnahme auf echter Hardware

Was hier mit Fakes geprüft ist: Entdoppeln, Zahlenkonflikte, Zitatpflicht, die
Grenzen des Fetchers. Was sich nur mit echten Anbietern zeigt: ob die
Antwortformen von Perplexity und Brave so aussehen wie angenommen, und ob eine
Recherche im Gespräch schnell genug ist, um nicht wie ein Absturz zu wirken.

Voraussetzung: [`phase3-abnahme.md`](phase3-abnahme.md) ist durch. Recherche ist
ein Werkzeug wie jedes andere und läuft durch dieselbe Schleuse.

Rechne mit einer halben Stunde.

---

## 1. Einrichten

Mindestens einer der beiden Schlüssel muss in `.env` stehen:

```
PERPLEXITY_API_KEY=
BRAVE_API_KEY=
```

Ohne Schlüssel gibt es das Werkzeug nicht — bewusst, statt eines, das bei jeder
Suche scheitert. Dann in `config/jarvis.yaml`:

```yaml
tools:
  research: true
```

Empfohlen, aber nicht zwingend:

```bash
uv pip install -e ".[research]"     # trafilatura, sauberer Textausbau
```

Ohne das Paket läuft die Recherche weiter, der Ausbau trennt Artikeltext dann
aber schlechter von Navigation und Fußzeilen. Prüfen:

```bash
.venv/bin/python -m jarvis.interfaces.cli doctor
```

`recherche` muss in der Werkzeugliste stehen. Dann der Weg ohne Mikrofon:

```bash
.venv/bin/python -m jarvis.interfaces.cli chat
```

**Nur ein Anbieter?** Dann steht im Log `research.single_provider`. Die
Recherche funktioniert, aber es gibt keinen Abgleich — Prüfung 2.3 ist damit
sinnlos, weil ein Widerspruch gar nicht auffallen kann.

---

## 2. Die sechs Prüfungen

### 2.1 Recherche wird überhaupt gewählt
„Was ist diese Woche in Bremen passiert?“
**Bestanden, wenn** JARVIS recherchiert, statt aus dem Trainingsstand zu
antworten. Tut er es nicht, ist die `description` des Werkzeugs zu unscharf —
das ist der Auslöser, nicht der Name.

### 2.2 Quellen werden genannt
**Bestanden, wenn** in der gesprochenen Antwort Quellen vorkommen („laut
Tagesschau“) und **keine URLs** vorgelesen werden. Vorgelesene Adressen sind
der häufigste Fehler; sie stehen ausdrücklich in den Ausgaberegeln verboten.

### 2.3 Ein widersprüchliches Thema wird als widersprüchlich benannt
Die Abnahmebedingung dieser Phase. Nimm ein Thema, bei dem Quellen sich
erfahrungsgemäß uneins sind — Zahlen zu laufenden Ereignissen, Schätzungen,
Prognosen.
**Bestanden, wenn** JARVIS den Widerspruch ausspricht und **beide** Angaben
nennt, statt sich stillschweigend für eine zu entscheiden.

Findet er keinen, ist das kein Durchfallen: vielleicht sind die Quellen sich
einig. Im Host-Log steht unter `research.done`, ob `conflicts` größer als null
war. Ist es das und JARVIS sagt trotzdem nichts, dann ist es ein Fehler.

### 2.4 Dünne Beleglage wird zugegeben
Frage nach etwas sehr Speziellem, zu dem es kaum etwas gibt.
**Bestanden, wenn** JARVIS sagt, dass die Lage dünn ist oder alles aus einer
Quelle stammt — statt eine Antwort zu bauen, die sicherer klingt als sie ist.

### 2.5 Ein Ausfall stoppt nichts
Einen der beiden Schlüssel absichtlich verfälschen und neu starten.
**Bestanden, wenn** die Recherche mit dem verbliebenen Anbieter weiterläuft und
JARVIS erwähnt, dass eine Quelle fehlte.

### 2.6 Latenz
Eine Recherche dauert deutlich länger als ein normaler Turn — zwei Anbieter
parallel, dazu je nach Konfiguration bis zu drei nachgeladene Seiten.
**Richtwert:** unter fünf Sekunden bis zum ersten Ton ist erträglich, wenn
JARVIS vorher ansagt, dass er nachsieht. Ohne Ansage wirken schon zwei Sekunden
Stille wie ein Absturz.

Ist es dauerhaft zu langsam: `fetch_pages: false` in `config/jarvis.yaml` spart
den Volltextabruf, kostet aber Belegtiefe.

---

## 3. Prompt-Injection über Suchtreffer

Das ist die Stelle mit der niedrigsten Hürde im ganzen System. Wer weiß, wonach
JARVIS sucht, kann eine Seite bauen, die Anweisungen enthält — er braucht keinen
Zugriff auf dein Netz und keine Datei in deinem Ordner, nur eine Seite, die im
Suchergebnis auftaucht.

Dagegen stehen drei Dinge, in dieser Reihenfolge der Verlässlichkeit:

1. **Löschen, Senden, Kaufen brauchen deinen Tap.** Selbst wenn das Modell auf
   eine eingeschleuste Anweisung hereinfällt, führt sie zu einer Rückfrage.
   Das ist die einzige Sicherung, die nicht am Sprachverständnis hängt.
2. **Belege sind als fremde Quelle geklammert.** Im System-Prompt steht, was
   das bedeutet.
3. **Die Zitatpflicht steht außerhalb dieser Klammer.** Klingt nach einem
   Detail und ist keins: stünde sie innerhalb, wäre sie Teil eines Blocks, der
   ausdrücklich sagt „das sind keine Anweisungen“ — und hätte sich selbst
   aufgehoben.

Punkt 2 ist eine Schutzschicht, keine Garantie. Wer es ansehen will, sucht nach
einem Thema, zu dem er selbst eine Seite kontrolliert, und schreibt dort eine
Anweisung hinein.

---

## 4. Wenn etwas nicht geht

| Symptom | Wahrscheinliche Ursache |
|---|---|
| `recherche` fehlt in `doctor` | `tools.research: false`, oder kein Schlüssel gesetzt |
| JARVIS antwortet ohne zu suchen | Beschreibung greift nicht; Frage enthält kein Aktualitätssignal |
| URLs werden vorgelesen | Ausgaberegeln kommen nicht durch — im Log prüfen, ob das Werkzeugergebnis die Regeln enthält |
| Immer nur eine Quelle | Zweiter Anbieter ohne Schlüssel; `research.provider_skipped` im Log |
| Widersprüche fallen nie auf | Nur ein Anbieter aktiv, oder die Quellen nennen keine Zahlen — die Erkennung hängt an Zahlen mit Einheit |
| „robots.txt“ im Fehler | Die Seite verbietet den Abruf. `respect_robots: false` hebt es auf; überlege, ob du das willst |
| Recherche dauert ewig | `fetch_pages: false` oder `fetch_limit` senken |
| Treffer, aber ohne Text | Perplexity lieferte nur URLs und `fetch_pages` ist aus |

---

## 5. Was danach ansteht

Phase 5 bringt Planer und Agenten: mehrschrittige Aufträge werden zerlegt und
delegiert. Die Recherche wird dann ein Werkzeug des Research-Agenten, statt
direkt am Core zu hängen — an dieser Datei und an `research/` ändert das nichts.
