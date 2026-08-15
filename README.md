# JARVIS

Persönlicher, sprachgesteuerter KI-Assistent. Kein Chatbot: modulare Plattform mit
austauschbaren Providern (STT/TTS/LLM/Research), Tool- und Agentensystem,
mehrschichtigem Gedächtnis, Sicherheitsmodell mit Bestätigungspflicht und HUD.

**Stand: Phase 3 — Werkzeuge und Sicherheitsmodell.** Die Sprachschleife aus
Phase 2 steht; dazugekommen sind Werkzeuge mit Risikostufen, Bestätigungs-
pflicht und ein Browser-Agent. Alles Weitere folgt in Phasen; der vollständige
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
│ Bestätigungsdialog            │        │ (ab Phase 4: Research)        │
└───────────────────────────────┘        └───────────────────────────────┘
```

## Schnellstart (Host)

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"          # Fake-Provider, keine Keys nötig
uv pip install -e ".[providers]"    # zusätzlich für echte Provider
uv pip install -e ".[browser]"      # nur für den Browser-Agenten (Phase 3)

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

## Datenschutz in einem Satz

Audio wird nirgends gespeichert, Provider-Keys liegen ausschließlich auf dem
Host, und im Cloud-Modus verlässt Rohaudio bewusst das eigene Netz — ebenso
alles, was ein Werkzeug liest. Was genau wohin geht, steht in
[`docs/data-flows.md`](docs/data-flows.md).
