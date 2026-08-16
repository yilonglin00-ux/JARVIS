# JARVIS

Persönlicher, sprachgesteuerter KI-Assistent. Kein Chatbot: modulare Plattform mit
austauschbaren Providern (STT/TTS/LLM/Research), Tool- und Agentensystem,
mehrschichtigem Gedächtnis, Sicherheitsmodell mit Bestätigungspflicht und HUD.

**Stand: Phase 4 — Recherche mit Belegen.** Sprachschleife (Phase 2) und
Werkzeuge mit Risikostufen und Bestätigungspflicht (Phase 3) stehen; dazu-
gekommen ist Mehrquellen-Recherche, die Widersprüche benennt statt sie
wegzurechnen. Alles Weitere folgt in Phasen; der vollständige
Entwurf steht in [`docs/architektur.md`](docs/architektur.md).

## Aufbau

Der Core läuft **nicht** auf dem iPad — iPadOS erlaubt keine beliebigen
Python-Prozesse, kein Playwright, keine Hintergrunddienste. Also
Client-Server, mit dem Audio-Ein- und -Ausgang auf dem Client:

```
┌─── iPad (Client) ─────────────┐        ┌─── Host (Mac mini / Linux) ───┐
│ Mikrofon (getUserMedia + AEC) │        │ JARVIS Core (Python)          │
│ AudioWorklet → 16 kHz PCM     │◀─wss──▶│ STT · LLM · TTS               │
│ VAD → Barge-in                │        │ Session · Event-Bus           │
│ AudioWorklet-Player + Analyser│        │ Werkzeuge · Policy · Browser  │
│ Bestätigungsdialog            │        │ Recherche · Belege · Zitate   │
└───────────────────────────────┘        └───────────────────────────────┘
```

## Schnellstart (Host)

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"          # Fake-Provider, keine Keys nötig
uv pip install -e ".[providers]"    # zusätzlich für echte Provider
uv pip install -e ".[browser]"      # nur für den Browser-Agenten (Phase 3)
uv pip install -e ".[research]"     # sauberer Textausbau für die Recherche

cp .env.example .env
.venv/bin/python -m jarvis.interfaces.cli token   # Token nach .env kopieren
.venv/bin/python -m jarvis.interfaces.cli doctor  # prüft Config und Keys
.venv/bin/python -m jarvis.interfaces.cli serve
```

Ohne API-Keys ausprobieren: in `config/jarvis.yaml` alle drei Provider auf
`fake` setzen. Der komplette Weg läuft dann durch, nur ohne echte Stimme.

Textmodus im Terminal, ohne Mikrofon und ohne Client:

```bash
.venv/bin/python -m jarvis.interfaces.cli chat
```

## Schnellstart (Client)

```bash
cd ui
npm install
npm run dev     # http://<host>:5173
```

Safari gibt das Mikrofon nur unter HTTPS oder auf `localhost` frei. Für die
Arbeit vom iPad aus also Tailscale (bringt TLS mit) oder ein lokales
Zertifikat — die Schritte stehen in [`docs/phase2-abnahme.md`](docs/phase2-abnahme.md).

## Prüfen

```bash
.venv/bin/python -m pytest      # läuft ohne API-Keys und ohne Mikrofon
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy
cd ui && npm run typecheck
```

## Wo was liegt

| Pfad | Inhalt |
|---|---|
| `src/jarvis/core/` | Event-Bus, Session/Turn, `JarvisCore` — kennt nur Interfaces |
| `src/jarvis/voice/` | Sprachschleife, Satz-Chunker, Barge-in, STT-/TTS-Adapter |
| `src/jarvis/llm/` | LLM-Abstraktion, Router, Anthropic-Adapter |
| `src/jarvis/security/` | Risikostufen, Policy-Engine, Bestätigungsfluss |
| `src/jarvis/tools/` | Werkzeug-ABC, Verzeichnis mit Schleuse, Builtins |
| `src/jarvis/browser/` | Browser-Abstraktion, Playwright-Backend, Fake |
| `src/jarvis/research/` | Recherchestrecke: Provider, Fetcher, Evaluator, Werkzeug |
| `src/jarvis/interfaces/` | WS-Protokoll, FastAPI-Server, Auth, CLI |
| `src/jarvis/factory.py` | einzige Stelle, die konkrete Provider kennt |
| `ui/src/lib/` | WS-Client, Audio-Capture/Player, VAD |
| `ui/public/worklets/` | AudioWorklets — Aufnahme und sofort leerbare Wiedergabe |
| `config/policies.yaml` | was ohne Rückfrage erlaubt ist — und was nicht |
| `docs/` | Architektur, Abnahme, Datenflüsse |

## Werkzeuge und Sicherheit

JARVIS kann seit Phase 3 handeln, nicht nur reden: Uhrzeit, Notizen, Dateien in
einem Sandkasten, optional ein Browser-Agent. Jede Aktion hat eine Risikostufe,
und ab `SENSITIVE` fragt er nach — konkret, mit Klartext und den echten Werten.

Drei Regeln stehen im Code, nicht in der Konfiguration:

- **Die Rückfrage sitzt im Verzeichnis, nicht im Werkzeug.** Ein Werkzeug kann
  seine Frage besser formulieren, aber nicht weglassen.
- **Stufen lassen sich nur anheben.** `config/policies.yaml` kann strenger
  sein als der Code, nie milder.
- **Keine Antwort ist eine Ablehnung.** Zeitablauf, Abbruch und Barge-in führen
  alle dazu, dass die Aktion nicht stattfindet.

Was erlaubt ist, steht in [`config/policies.yaml`](config/policies.yaml). Der
Browser ist ab Werk aus und braucht zusätzlich eine Domain-Allowlist — eine
leere Liste heißt *nichts erlaubt*, nicht *alles erlaubt*.

## Recherche

Zwei unabhängige Suchwege (Perplexity Sonar und Brave), entdoppelt und nach
Aktualität geordnet. Belege bleiben **je Quelle getrennt** — nichts wird zu
einem Absatz verrechnet. Nur deshalb kann JARVIS „die Quellen sind sich nicht
einig“ sagen, statt sich unbemerkt für eine Seite zu entscheiden.

Widersprüche werden nicht *erkannt* — das ginge nur semantisch. Markiert wird,
was überprüfbar ist: zwei unabhängige Domains, die zur selben Einheit
verschiedene Zahlen nennen. Den Rest muss das Modell tun, mit einer Auflage, die
bewusst **außerhalb** der Untrusted-Klammer um die Belege steht.

## Datenschutz in einem Satz

Audio wird nirgends gespeichert, Provider-Keys liegen ausschließlich auf dem
Host, und im Cloud-Modus verlässt Rohaudio bewusst das eigene Netz — ebenso
alles, was ein Werkzeug liest. Was genau wohin geht, steht in
[`docs/data-flows.md`](docs/data-flows.md).
