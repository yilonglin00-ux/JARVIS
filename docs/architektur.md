# JARVIS — Technische Architektur

> **Status:** Phase 1 abgenommen, Phase 2 umgesetzt. Dieses Dokument ist der
> verbindliche Entwurf für alle Phasen. Was davon bereits Code ist, steht am
> Ende unter „Phase 2 — Konkreter Umsetzungsplan"; die Abnahmeschritte auf
> echter Hardware stehen in [`phase2-abnahme.md`](phase2-abnahme.md).

## Kontext

Das Repository `yilonglin00-ux/jarvis` ist leer (nur `README.md`, 1 Commit). Es gibt keinen bestehenden Code, keine Konventionen, keine Abhängigkeiten — echtes Greenfield.

Ziel ist **kein Chatbot**, sondern eine modulare, persönliche KI-Assistenten-Plattform: sprachgesteuert (zuhören + sprechen + unterbrechbar), mit austauschbaren Providern (STT/TTS/LLM/Research), Tool- und Agentensystem, mehrschichtigem Memory, einem Sicherheitsmodell mit Bestätigungspflicht und einem HUD-Interface im Stil des gezeigten Screenshots (inkl. Sprechblase und visueller Haptik während der Sprachausgabe).

**Deine Entscheidungen aus Phase 1:**
- Betriebsmodus: **Cloud-first** (lokaler Fallback bleibt eingebaut)
- Zielplattform: **iPadOS**
- Phase-2-Start: **Sprachschleife zuerst**, HUD minimal, vollständiges HUD in Phase 7

Dieses Dokument ist **Phase 1**. Es wird kein Produktivcode geschrieben, bevor du freigibst.

---

## 0. Die wichtigste Konsequenz aus „iPadOS"

**Auf dem iPad kann der JARVIS-Core nicht laufen.** iPadOS erlaubt keine beliebigen Python-Prozesse, kein PortAudio, kein Playwright, keine lokalen ML-Modelle in nennenswerter Größe und keine Hintergrunddienste. Ein vollständig nativer Nachbau in Swift würde bedeuten: kein Playwright (also kein echter Browser-Agent), kein Python-ML-Ökosystem, und jeder Provider müsste neu implementiert werden. Das widerspricht der Kernanforderung „modular und austauschbar".

**Deshalb: Client-Server-Architektur.** Das ist keine Notlösung, sondern fällt hier fast kostenlos an — die geforderte Trennung „Core ≠ Interface" mit einem WebSocket-Protokoll dazwischen war ohnehin geplant. Der einzige echte Unterschied: **Audio-Ein- und -Ausgabe wandern vom Server auf den Client.**

```
┌─── iPad (Client) ──────────────────┐        ┌─── Host (Mac mini / Linux / VM) ───┐
│  Mikrofon (getUserMedia + AEC)     │        │  JARVIS Core (Python)              │
│  AudioWorklet → 16 kHz PCM         │◀──────▶│  STT · TTS · LLM · Planner         │
│  Silero VAD (WASM) → Barge-in      │  WSS   │  Agents · Tools · Browser          │
│  Audio-Playback + AnalyserNode     │        │  Research · Memory · Security      │
│  HUD (React) + Sprechblase         │        │  Playwright (headless)             │
└────────────────────────────────────┘        └────────────────────────────────────┘
```

**Host-Empfehlung:** ein immer laufender Rechner im eigenen Netz (Mac mini, NUC, Raspberry Pi 5 für den Cloud-Modus ausreichend) mit **Tailscale**, damit das iPad auch unterwegs sicher zugreift — ohne Portfreigabe im Router. Alternativ eine kleine Cloud-VM; dann verlässt allerdings auch das Audio dein Netz zusätzlich zum Cloud-STT.

**Das iPad wird zuerst eine PWA** (Safari, „Zum Home-Bildschirm"), später optional eine **Capacitor-App** für Hintergrund-Audio, App-Icon und Push. Der HUD-Code bleibt identisch — Capacitor ist nur eine native Hülle um dieselbe React-App.

Drei iPad-spezifische Punkte, die die Architektur konkret verbessern oder einschränken — Details in §5 und §17:
- **Vorteil:** Der Browser liefert produktionsreife **Echo-Cancellation** (WebRTC-APM) frei Haus. Das war das größte technische Einzelrisiko der Desktop-Variante und ist damit weitgehend erledigt.
- **Einschränkung:** Immer-an-Wake-Word („Hey JARVIS" bei gesperrtem Bildschirm) ist auf iPadOS **nicht möglich**. Wake Word funktioniert nur bei geöffneter, aktiver App. Primärer Auslöser wird „Tap to Speak" / Push-to-Talk — was zum Screenshot passt.
- **Einschränkung:** iPads haben **keine Vibrations-Hardware**. `navigator.vibrate` existiert in Safari nicht. Echte Haptik entfällt — die von dir gewünschte *visuelle* Haptik ist auf dieser Plattform ohnehin die richtige und einzige Antwort.

---

## 1. Technologie-Entscheidungen (mit Begründung)

### Host / Core

| Bereich | Entscheidung | Warum |
|---|---|---|
| **Core-Sprache** | Python 3.12+ (`asyncio`) | Einziges Ökosystem mit erstklassigen Bindings für *alle* Bausteine gleichzeitig: alle LLM-SDKs, Playwright, Vektor-DBs, lokale STT (faster-whisper), Silero-VAD, openWakeWord. |
| **Async-Modell** | Durchgängig `asyncio`, ein Task pro Session | Barge-in erfordert echte Nebenläufigkeit: eingehende Audioframes, laufender LLM-Stream und ausgehende TTS-Chunks gleichzeitig, jeder einzeln abbrechbar. |
| **Transport** | FastAPI + WebSocket (`wss://`), ein bidirektionaler Kanal für JSON-Events **und** binäre Audioframes | Ein Kanal für alles hält Reihenfolge und Latenz kontrollierbar. Kein Polling, kein zweiter Socket. |
| **Auth** | Vorgeteilter Token + Tailscale-Netzgrenze; später Passkey | Der Core darf niemals offen im Internet stehen — er kann Mails senden und Browser fernsteuern. |
| **Browser-Automation** | Playwright (Python, async) | Wie gefordert. Auto-Waiting, Accessibility-Tree, Screenshots. Läuft auf dem Host, nicht auf dem iPad. |
| **Datenhaltung / Memory** | SQLite + `sqlite-vec` + SQLAlchemy | Eine Datei, kein Server, Volltext (FTS5) + Vektorsuche in derselben DB. Postgres/pgvector bleibt über das Repository-Pattern offen. |
| **Konfiguration** | `pydantic-settings`, YAML für Profil, `.env`/Keychain für Secrets | Typvalidiert, Secrets nie im Code. |
| **Package-Manager** | `uv` | Schnell, lockfile-basiert reproduzierbar. |
| **Tests** | pytest + pytest-asyncio, `Fake*`-Provider für jede Abstraktion | Der Core muss ohne API-Keys und ohne Mikrofon testbar sein. |

### iPad / Client

| Bereich | Entscheidung | Warum |
|---|---|---|
| **Framework** | React 19 + TypeScript + Vite + Tailwind + Framer Motion | Der Screenshot ist ein dichtes, animiertes Grid — dafür ist das der schnellste tragfähige Weg. |
| **Audio-Eingang** | `getUserMedia` mit `echoCancellation`, `noiseSuppression`, `autoGainControl` → `AudioWorklet` → 16 kHz PCM | AudioWorklet läuft im Audio-Thread, nicht im UI-Thread — kein Ruckeln bei Animationen. AEC gratis. |
| **Audio-Ausgang** | `AudioWorklet`-Player mit eigener Chunk-Queue (**nicht** `<audio>`) | Nur so ist der Puffer in einem Frame leerbar. `<audio>` spielt nach `pause()` noch Gepuffertes — Barge-in wäre unmöglich. |
| **VAD** | Silero VAD als ONNX über `onnxruntime-web` (WASM/SIMD) | Läuft clientseitig in ~1 ms/Frame. Barge-in wird lokal erkannt und lokal geflusht — ohne Netz-Roundtrip. |
| **Visualisierung** | Canvas 2D + `AnalyserNode`; `requestAnimationFrame` | 60 fps Waveform/Orb, direkt aus dem tatsächlich abgespielten Signal. |
| **Auslieferung** | PWA (Vite PWA Plugin, Manifest, Home-Bildschirm) → später Capacitor v7 | Sofort nutzbar, ohne Apple-Developer-Account. Capacitor nur, wenn Hintergrund-Audio oder Push nötig werden. |
| **State** | Zustand (Store) + typisierter WS-Client mit Auto-Reconnect | Verbindungsabbrüche sind auf Mobilgeräten Normalfall, nicht Ausnahme. |

### Provider-Auswahl (Stand August 2026, nach Recherche) — Cloud-first

Alle sind **austauschbar hinter Interfaces**. Die Auswahl unten ist der *Default*, nicht die Architektur.

**Speech-to-Text** (Priorität: Latenz, Streaming, DE+EN)
- Default: **Deepgram Flux** oder **ElevenLabs Scribe v2 Realtime** — echtes WebSocket-Streaming, Partial Results, schnelles Endpointing (Scribe v2 RT wirbt mit <150 ms, Deepgram mit sub-300 ms). AssemblyAI Universal-3.5 Realtime (~$0,45/h) als dritte Option.
- Fallback lokal: **faster-whisper** (`large-v3-turbo`) auf dem Host — für den Datenschutzmodus.
- Endgültige Wahl erst im Benchmark (Phase 2) mit *deinen* echten iPad-Mikrofonaufnahmen auf Deutsch. Anbieter-Benchmarks übertragen sich erfahrungsgemäß schlecht, und das iPad-Mikrofon nach WebRTC-AEC klingt anders als ein Studiomikrofon.

**Text-to-Speech** (Priorität: Latenz, natürliche deutsche Stimme, Chunk-Streaming)
- Default: **ElevenLabs Flash v2.5** (~250–300 ms TTFB, sehr gute deutsche Stimmen) oder **Cartesia Sonic** (schnellster gemessener TTFB, ~$0,05/1k Zeichen).
- Fallback lokal: **Piper** auf dem Host (CPU, deutsche Stimmen).
- Harte Anforderung: **Chunk-Streaming**, damit die Ausgabe innerhalb eines Frames abbrechbar ist. Ausgabeformat Opus (Bandbreite iPad↔Host) oder PCM im LAN.

**LLM**
- Default **Claude**: `claude-opus-5` für Planer und komplexe Agenten, `claude-sonnet-5` für Standard-Konversation, `claude-haiku-4-5` für Klassifikation, Routing und Memory-Extraktion. Adaptive Thinking (`thinking: {"type": "adaptive"}`) plus `output_config.effort` als Kosten-/Qualitätsregler, Prompt-Caching für den stabilen System-Prompt.
- Weitere Provider hinter demselben Interface: OpenAI, Gemini, **Ollama** (lokal auf dem Host).
- Der Core kennt nur `LLMProvider`, nie einen Anbieternamen.

**Research / Perplexity**
- Offizieller Weg existiert und ist sauber: **Perplexity Sonar API** (`api.perplexity.ai`, OpenAI-kompatibles Chat-Completions-Schema, Bearer-Token, liefert Citations) **und** ein **offizieller MCP-Server** (`@perplexity-ai/mcp-server`, Repo `perplexityai/modelcontextprotocol`, MIT).
- Entscheidung: **direkte HTTP-Integration der Sonar-API** als `PerplexityProvider` — weniger bewegliche Teile, volle Kontrolle über Timeouts und Kosten. Der offizielle MCP-Server bleibt als alternativer Adapter über den generischen MCP-Client (Phase 7) verfügbar.
- **Keine** Browser-Automation gegen perplexity.ai. Nicht nötig, da offizielle API vorhanden.

**Wake Word** (Phase 7, eingeschränkt — siehe §0)
- **openWakeWord** bringt ein *vortrainiertes* `hey_jarvis`-Modell mit (Open Source). Die ONNX-Modelle laufen über `onnxruntime-web` auch im Browser — aber nur bei aktiver, im Vordergrund geöffneter App.

---

## 2. Projektstruktur

```
jarvis/
├── pyproject.toml                # uv / Abhängigkeiten (Host)
├── .env.example                  # Keys als Platzhalter, NIE echte Werte
├── config/
│   ├── jarvis.yaml               # Profil: Sprache, Stimme, aktive Provider, Risikopolicy
│   └── policies.yaml             # Sicherheitsregeln pro Tool
├── src/jarvis/
│   ├── core/
│   │   ├── kernel.py             # JarvisCore — Orchestrator, kennt nur Interfaces
│   │   ├── events.py             # Event-Bus (pub/sub, typisiert)
│   │   ├── session.py            # Session-/Turn-Lifecycle, Cancellation-Scopes
│   │   └── errors.py
│   ├── voice/
│   │   ├── pipeline.py           # VoicePipeline: Audio-In → STT → Core → TTS → Audio-Out
│   │   ├── audio_stream.py       # Frames vom/zum Client (kein lokales Audiogerät!)
│   │   ├── barge_in.py           # Interruption-Controller (siehe §5)
│   │   ├── vad_server.py         # Silero serverseitig — nur für lokale/Headless-Tests
│   │   ├── stt/
│   │   │   ├── base.py           # SpeechToText (ABC)
│   │   │   ├── deepgram.py  elevenlabs.py  whisper_local.py  fake.py
│   │   └── tts/
│   │       ├── base.py           # TextToSpeech (ABC)
│   │       ├── elevenlabs.py  cartesia.py  piper_local.py  fake.py
│   ├── llm/
│   │   ├── base.py               # LLMProvider (ABC), Message/ToolCall/StreamChunk
│   │   ├── router.py             # Modellwahl nach Aufgabenklasse + Budget
│   │   ├── anthropic.py  openai.py  gemini.py  ollama.py  fake.py
│   ├── memory/
│   │   ├── base.py               # MemoryStore (ABC)
│   │   ├── short_term.py  working.py  long_term.py
│   │   ├── embeddings.py
│   │   └── store_sqlite.py       # SQLite + sqlite-vec
│   ├── planner/
│   │   ├── planner.py  intent.py  plan.py
│   ├── agents/
│   │   ├── base.py  registry.py
│   │   ├── conversation.py  research.py  browser.py  coding.py  planning.py
│   ├── tools/
│   │   ├── base.py  registry.py  result.py
│   │   └── builtin/              # time, weather, notes, files, calendar …
│   ├── browser/
│   │   ├── manager.py  page_agent.py  extractor.py  browser_tool.py
│   ├── research/
│   │   ├── pipeline.py  fetcher.py  extractor.py  evaluator.py
│   │   └── providers/
│   │       ├── base.py  perplexity.py  websearch.py  fake.py
│   ├── security/
│   │   ├── risk.py  policy.py  confirm.py  redaction.py
│   ├── integrations/             # ab Phase 7: github, notion, calendar, mail, smarthome, mcp/
│   ├── interfaces/
│   │   ├── server.py             # FastAPI + WebSocket-Endpunkt (der einzige Zugang)
│   │   ├── protocol.py           # Typisierte WS-Nachrichten, gespiegelt in ui/src/lib/protocol.ts
│   │   ├── auth.py               # Token-Prüfung, Rate-Limit
│   │   └── cli.py                # Text-Interface für Entwicklung/Headless
│   └── config/settings.py
├── ui/                           # React/TS HUD — läuft auf dem iPad
│   ├── public/manifest.webmanifest, icons/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── lib/
│   │   │   ├── ws.ts             # WS-Client, Reconnect, Backoff
│   │   │   ├── protocol.ts       # aus protocol.py generierte Typen
│   │   │   └── audio/
│   │   │       ├── capture.ts        # getUserMedia + AudioWorklet → PCM
│   │   │       ├── player.ts         # AudioWorklet-Playback mit flush()
│   │   │       ├── vad.ts            # Silero ONNX (onnxruntime-web)
│   │   │       ├── wakeword.ts       # openWakeWord ONNX (Phase 7)
│   │   │       └── worklets/*.js
│   │   ├── state/store.ts
│   │   └── components/
│   │       ├── Sidebar.tsx  TopBar.tsx  StatusStrip.tsx
│   │       ├── CoreOrb.tsx           # zentrale Sphäre + Orbits
│   │       ├── SpeechBubble.tsx      # Sprechblase bei Sprachausgabe
│   │       ├── VoiceVisualizer.tsx   # Waveform/Pegel aus AnalyserNode
│   │       ├── HapticPulse.tsx       # visuelle Haptik (Ring-Pulse)
│   │       ├── AgentGrid.tsx  ToolPanel.tsx  MemoryPanel.tsx
│   │       ├── Timeline.tsx  IntelFeed.tsx  SystemMonitor.tsx
│   │       └── ConfirmDialog.tsx     # Sicherheitsbestätigung
└── tests/
```

**Leitprinzip:** Abhängigkeiten zeigen nur nach innen. `core/` importiert *keine* konkreten Provider — nur die ABCs. Provider werden beim Start per Factory aus der Config gewählt und injiziert. Jeder Baustein ist austauschbar, ohne den Core anzufassen. Das gilt auch für den Client: die UI kennt nur `protocol.ts`, nie einen Provider.

---

## 3. Datenfluss

### Sprach-Turn (Ende zu Ende, iPad ↔ Host)

```
[iPad] Mikrofon → getUserMedia (AEC/NS/AGC an)
         └→ AudioWorklet → 20-ms-Frames, 16 kHz mono
              ├→ Silero VAD (WASM)   ── lokal, IMMER aktiv, auch während Wiedergabe
              │      └→ Sprache erkannt während state=speaking? → BARGE-IN (§5)
              └→ WS binary ↑
                    │
[Host]  ────────────┴→ STTProvider (Stream) → partial / final Transcript
                                   │  (partial geht sofort per WS ↓ an die UI)
                                   ▼
                            JarvisCore.handle_utterance()
                                   │
                       ┌───────────┴────────────┐
                       ▼                        ▼
                ShortTermMemory           Planner.plan()
               (Kontext laden)      Intent + Tool-/Agent-Bedarf
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
             direkte Antwort        Tool-Ausführung        Agent-Delegation
             (Konversation)         (via Registry)         (Research/Browser/…)
                                          │                     │
                                          ▼                     ▼
                                  SecurityPolicy.check() → ggf. ConfirmDialog (WS ↓↑)
                                          │
                                          ▼
                                     ToolResult(s)
                                          │
                                          ▼
                        LLMProvider.stream()  ← Kontext + Ergebnisse + Memory
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                           ▼
        WS ↓ text.delta                              TTSProvider.stream()
                    │                                           │
                    │                                     WS ↓ audio.chunk (Opus/PCM)
                    ▼                                           ▼
[iPad]   Sprechblase tippt mit              AudioWorklet-Player → Lautsprecher
                    │                                           │
                    └──────────────► AnalyserNode ──────────────┘
                                          ▼
                          VoiceVisualizer + HapticPulse (60 fps)
                                          │
[Host]                                    ▼
                            MemoryWriter (Turn + extrahierte Fakten)
```

### Beispiel „JARVIS, recherchiere die neuesten KI-News und fasse sie zusammen."

```
iPad-Audio → Host-STT → Core → Planner: {intent: research, agent: ResearchAgent}
  → ResearchAgent → ResearchTool
      → PerplexityProvider.search()   (Sonar, mit Citations)
      → WebSearchProvider.search()    (parallel, für Cross-Check)
      → Fetcher + Extractor für Top-N Quellen
      → Evaluator: Dedup, Recency, Domain-Reputation, Widersprüche
  → LLM synthetisiert mit Quellenangaben
  → TTS-Chunks → iPad spricht; Sprechblase zeigt Text, Panel zeigt Quellenliste
  → LongTermMemory: „interessiert sich für KI-News" (Präferenz-Signal)
```

**Bandbreite:** Upstream 16 kHz/16 bit mono ≈ 32 kB/s (im LAN unkritisch; über Mobilfunk optional Opus, dann ~4 kB/s). Downstream TTS als Opus ≈ 6 kB/s. Netz-Latenz LAN ~1–5 ms, über Tailscale/Mobilfunk ~20–60 ms — beides klein gegenüber den 300–800 ms der KI-Kette.

---

## 4. Voice-Architektur

**Zustandsautomat (Quelle der Wahrheit ist der Host, das iPad spiegelt ihn):**

```
IDLE ──(Tap to Speak | Push-to-Talk | Wake Word ab Ph. 7)──▶ LISTENING
LISTENING ──(VAD: Sprachende / Endpointing durch STT)──▶ PROCESSING
PROCESSING ──(erste TTS-Chunks am Client abgespielt)──▶ SPEAKING
SPEAKING ──(Wiedergabe fertig)──▶ IDLE | LISTENING (Konversationsmodus)
SPEAKING ──(BARGE-IN, clientseitig erkannt)──▶ INTERRUPTED ──▶ LISTENING
jeder Zustand ──(Verbindungsverlust)──▶ DISCONNECTED (UI zeigt es sichtbar)
```

**Interfaces — bewusst minimal, damit Provider leicht austauschbar bleiben:**

```python
class SpeechToText(ABC):
    async def transcribe_stream(
        self, audio: AsyncIterator[bytes], *, language: str | None
    ) -> AsyncIterator[Transcript]: ...        # Transcript(text, is_final, confidence)
    @property
    def supports_streaming(self) -> bool: ...
    @property
    def languages(self) -> set[str]: ...

class TextToSpeech(ABC):
    async def synthesize_stream(
        self, text: AsyncIterator[str] | str, *, voice: VoiceSpec
    ) -> AsyncIterator[AudioChunk]: ...
    async def stop(self) -> None: ...          # muss sofort greifen
```

`VoiceSpec` kapselt von Anfang an `voice_id`, `language`, `speed`, `pitch`, `volume` — auch wenn ein Provider nur einen Teil unterstützt (Rest wird geloggt und ignoriert, nicht als Fehler behandelt).

**Warum `AsyncIterator[str]` als TTS-Eingabe:** So beginnt die Sprachausgabe, während das LLM noch schreibt (Chunking an Satzgrenzen). Das ist der größte Latenzhebel im System — ohne ihn wartet man LLM-Zeit *plus* TTS-Zeit statt nur bis zum ersten Satz.

---

## 5. Unterbrechungen (Barge-in)

Der kritischste Teil — und auf dem iPad in zwei Punkten *einfacher* als auf dem Desktop.

1. **Mikrofon und VAD laufen durchgehend weiter**, auch während JARVIS spricht. Ohne das ist Barge-in prinzipiell unmöglich. Viele Implementierungen schalten während der Ausgabe das Mikrofon ab.
2. **Echo-Problem ist weitgehend gelöst:** `getUserMedia({audio: {echoCancellation: true}})` aktiviert die WebRTC-Echounterdrückung von Safari. Die ist ausgereift und rechnet die eigene Ausgabe aus dem Mikrofonsignal heraus — genau das Problem, das auf dem Desktop eigenen AEC-Code erfordert hätte. Zusätzlich: Energie-Gating gegen den bekannten eigenen Ausgabepegel als zweite Absicherung.
3. **Erkennung geschieht clientseitig, nicht auf dem Host.** Das ist die entscheidende Designentscheidung: Ein Roundtrip iPad→Host→iPad kostet 40–120 ms, das Flush-Ziel liegt bei <60 ms. Also erkennt Silero-VAD im Browser, der Client stoppt *sofort lokal* und meldet dem Host erst danach `user.interrupt`.
4. **Schwelle:** Erst ab **≥ 200 ms zusammenhängender Sprache** über der VAD-Schwelle als echte Unterbrechung werten. Kürzeres („mhm", „okay", Husten) ist ein *Backchannel* und wird ignoriert. Falsch-Unterbrechungen sind für das Gefühl schlimmer als 200 ms Verzögerung.
5. **Flush-Pfad:**
   - *Client:* `AudioPlayer.flush()` — Worklet-Ringpuffer sofort leeren (deshalb kein `<audio>`-Element, das nach `pause()` noch Gepuffertes abspielt), eingehende `audio.chunk` verwerfen, Sprechblase kollabieren, Zustand → LISTENING, `user.interrupt` senden.
   - *Host:* TTS-Provider `stop()`, LLM-Stream über `CancelScope` abbrechen, laufende Tool-Aufrufe abbrechen (soweit abbrechbar), **tatsächlich gesprochenen Text-Präfix** ermitteln und *nur diesen* in den Verlauf schreiben. Nicht die vollständig generierte Antwort — sonst glaubt JARVIS, du hättest etwas gehört, das nie erklang.
6. **Zielwerte:** Client-Flush < 60 ms, Turn-Gap 200–400 ms, Falsch-Barge-in-Rate < 2 %.
7. **Sichtbare Bestätigung:** Sprechblase kollabiert, Orb wechselt von Speaking auf Listening. Ohne visuelle Rückmeldung wiederholt sich der Nutzer.

---

## 6. LLM-Architektur

```python
class LLMProvider(ABC):
    async def complete(self, req: LLMRequest) -> LLMResponse: ...
    async def stream(self, req: LLMRequest) -> AsyncIterator[StreamChunk]: ...
    @property
    def capabilities(self) -> Capabilities: ...  # tools, vision, thinking, ctx-window, Preis
```

Neutrale Datentypen (`Message`, `ToolCall`, `ToolResult`, `StreamChunk`) — kein Anbieter-Schema leckt in den Core. Jeder Adapter übersetzt in beide Richtungen.

**Router** wählt nach Aufgabenklasse *und* Budget:

| Aufgabe | Modell | Effort |
|---|---|---|
| Intent-Klassifikation, Routing, Memory-Extraktion | `claude-haiku-4-5` | — |
| Konversation, einfache Tool-Turns | `claude-sonnet-5` | `medium` |
| Planer, Research-Synthese, Coding-Agent | `claude-opus-5` | `high` |
| Offline-/Datenschutzmodus | Ollama auf dem Host | — |

Anthropic-spezifisch, aber über `Capabilities` gekapselt: adaptives Thinking statt fester Token-Budgets, `effort` als Kostenregler, **Prompt-Caching** für System-Prompt und Tool-Schemas (großer Hebel bei einem Assistenten, der denselben Prompt hundertfach täglich sendet), Streaming durchgängig.

---

## 7. Browser-Architektur

Läuft vollständig auf dem Host. Das iPad sieht nur Ergebnisse und optional Screenshots.

```
BrowserManager (Playwright-Lifecycle, persistenter Kontext für Logins)
   └─ BrowserSession (isolierter Kontext, eigene Cookies)
        ├─ navigate / back / reload
        ├─ read_page()      → Accessibility-Tree + trafilatura → Markdown (LLM-tauglich)
        ├─ find(description) → Element-Auflösung über Rolle/Name/Text statt CSS-Selektoren
        ├─ click / fill / select / scroll
        ├─ screenshot()     → an die UI und optional an ein Vision-Modell
        └─ extract(schema)  → strukturierte Daten per Structured Outputs
```

- **Accessibility-Tree statt roher DOM** als LLM-Eingabe: 10–50× kleiner und semantisch näher an dem, was der Nutzer meint. Roher DOM sprengt jedes Kontextfenster.
- **Element-Adressierung über Beschreibung** statt generierter CSS-Selektoren — robuster gegen Layout-Änderungen.
- **Live-Ansicht auf dem iPad:** Screenshots im Sekundentakt als WS-Frames, wenn der Browser-Agent arbeitet. Das ersetzt das fehlende „daneben sitzen und zuschauen".
- **Sicherheit:** Der Browser ist das gefährlichste Tool. Domain-Allowlist im Standardbetrieb; Formularabsendung und Login mindestens `SENSITIVE`, alles mit Zahlungsbezug `DESTRUCTIVE`. Downloads in ein Quarantäneverzeichnis. Eigenes Profil, getrennt vom persönlichen Browser.
- **Prompt-Injection ist real:** Webseiteninhalte sind *nie* Anweisungen. Fremdinhalte werden im Prompt als untrusted markiert; aus gelesenem Seiteninhalt darf der Agent keine Aktion ableiten, die die Risikostufe erhöht.

---

## 8. Research-/Perplexity-Architektur

```
ResearchPipeline
  ├─ QueryPlanner       → 1 Frage → n Suchanfragen (DE + EN)
  ├─ ResearchProvider[] → parallel
  │     ├─ PerplexityProvider   (Sonar API, offiziell, liefert Citations)
  │     ├─ WebSearchProvider    (Brave/Tavily/SearXNG — austauschbar)
  │     └─ FutureProvider
  ├─ Fetcher            → httpx, Timeout, Größenlimit, Robots-Respekt
  ├─ Extractor          → trafilatura → sauberer Fließtext
  ├─ Evaluator          → Dedup, Recency, Domain-Reputation, Widerspruchsmarkierung
  └─ Synthesizer        → LLM, Pflicht: jede Aussage mit Quelle
```

**Perplexity konkret:** Der Core kennt Perplexity nicht. Nur `ResearchProvider` ist bekannt; `PerplexityProvider` ist eine von mehreren Implementierungen, per Config an- und abschaltbar. Integration über die **offizielle Sonar API** (OpenAI-kompatibles Schema, Bearer-Auth). Modellwahl: `sonar` ($1/$1 pro Mio. Token) für Standardsuchen, `sonar-pro` ($3/$15) für Tiefenrecherche. Der offizielle MCP-Server bleibt als alternativer Adapter offen.

**Cross-Check:** Widersprechen sich Perplexity und Websuche, wird der Widerspruch *benannt* statt aufgelöst. JARVIS soll keine falsche Sicherheit vortäuschen.

---

## 9. Tool-System

```python
class Tool(ABC):
    name: str
    description: str                 # das ist der Trigger — präzise formulieren
    input_schema: dict               # JSON Schema
    risk: RiskLevel
    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult: ...
```

`ToolRegistry`: Registrierung per Dekorator, Lookup nach Name, Export der Schemas ins LLM-Format, Filterung nach aktivem Agent — nicht jeder Agent sieht jedes Tool. Das ist gleichzeitig Sicherheits- und Qualitätsmaßnahme.

`ToolResult` trägt immer `ok`, `data`, `display_text` (was JARVIS sagen kann), `citations`, `cost`, `duration`.

`ToolContext` gibt Zugriff auf Working Memory, Confirmation-Callback, Cancellation-Token und Event-Emitter (Live-Status in der UI).

**Phase 3 (Startset):** `time`, `weather`, `notes`, `files` (sandboxed), `browser`, `research`.
**Später:** `calendar`, `email`, `github`, `terminal`, `smarthome`, `database`, plus generischer **MCP-Client**, der beliebige MCP-Server als Tools einhängt.

---

## 10. Agentensystem

```python
class Agent(ABC):
    name: str
    description: str                 # wofür der Core ihn wählt
    allowed_tools: list[str]
    system_prompt: str
    model_hint: str
    async def run(self, task: Task, ctx: AgentContext) -> AgentResult: ...
```

| Agent | Aufgabe | Tools |
|---|---|---|
| Conversation | Dialog, Smalltalk, Rückfragen | keine / Memory |
| Research | Mehrquellen-Recherche + Synthese | research, browser (lesend) |
| Browser | Interaktive Web-Aufgaben | browser (voll) |
| Coding | Code lesen/schreiben/ausführen | files, terminal |
| Planning | Zerlegung komplexer Aufträge | keine (nur Denken) |

Der Core orchestriert; Agenten kennen einander nicht. Kommunikation ausschließlich über `Task` rein / `AgentResult` raus. Ergebnisse landen im Working Memory, nicht im Kontext anderer Agenten — das hält Kontextfenster klein und Fehler lokal.

Jeder Agent hat ein **Schritt-Budget** und ein **Kosten-Budget**. Beim Überschreiten bricht er kontrolliert ab und meldet Teilergebnisse, statt endlos zu schleifen.

---

## 11. Memory-System

| Schicht | Inhalt | Speicher | Lebensdauer |
|---|---|---|---|
| **Short-Term** | Aktuelle Konversation, letzte N Turns | RAM + SQLite-Journal | Session; danach kompaktiert |
| **Working** | Zwischenergebnisse einer laufenden Aufgabe | RAM, pro Task | Ende der Aufgabe |
| **Long-Term** | Fakten, Präferenzen, Personen, Projekte | SQLite + `sqlite-vec` + FTS5 | Dauerhaft, versioniert |
| **Episodic** | „Was haben wir am 3. März besprochen?" | dieselbe DB, Zeitindex | Dauerhaft, löschbar |

```python
class MemoryStore(ABC):
    async def remember(self, item: MemoryItem) -> str: ...
    async def recall(self, query: str, *, k: int, filters: Filters) -> list[MemoryItem]: ...
    async def forget(self, id: str) -> None: ...
    async def update(self, id: str, item: MemoryItem) -> None: ...
```

**Hybrid-Retrieval:** Vektorsuche + FTS5-Keyword-Suche, gemischt per Reciprocal Rank Fusion. Reine Vektorsuche versagt bei Eigennamen und exakten Bezeichnern, reine Keyword-Suche bei Umschreibungen.

**Schreibpfad:** Nicht jeder Satz wird gespeichert. Nach jedem Turn läuft `haiku` als Extraktor: „Enthält dieser Turn dauerhaft relevante Fakten oder Präferenzen?" Nur dann wird geschrieben, inklusive Dedup gegen Bestehendes. Sonst wächst das Long-Term-Memory zu unbrauchbarem Rauschen.

**Kontrollierbarkeit:** Jeder Eintrag ist im Memory-Panel des HUD sichtbar, editierbar und löschbar. Das ist keine Komfort-, sondern eine Vertrauensfunktion.

---

## 12. Sicherheitsmodell

```python
class RiskLevel(IntEnum):
    READ        = 0   # Wetter abfragen, Webseite lesen, Notiz lesen
    LOW         = 1   # Notiz anlegen, Datei im Sandbox-Verzeichnis schreiben
    SENSITIVE   = 2   # E-Mail senden, Kalendereintrag, Formular absenden, Commit
    DESTRUCTIVE = 3   # Datei löschen, Zahlung, force-push, Smart-Home-Sicherheit
```

- **READ / LOW:** laufen ohne Rückfrage, werden protokolliert.
- **SENSITIVE:** Bestätigung erforderlich. JARVIS fragt *konkret*, nicht generisch: „Soll ich die E-Mail an Anna mit dem Betreff ‚Angebot' wirklich senden?" Ein generisches „Bist du sicher?" trainiert reflexhaftes Ja.
- **DESTRUCTIVE:** Bestätigung + Wiederholung der genauen Aktion + Bestätigung **im HUD** (Tap), nicht per Sprache allein. Optional PIN.
- **Policy-Engine** (`policies.yaml`) kann Stufen pro Tool anheben (nie unter einen konfigurierten Boden senken), Domain-/Pfad-Allowlists definieren und Zeitfenster setzen.
- **Bestätigung ist eine erstklassige Interaktion:** `confirm.request` geht über den Event-Bus an alle aktiven Clients (HUD-Dialog + Sprachfrage). Timeout = Ablehnung, nie stille Ausführung.
- **Kill-Switch:** globales „Stopp" (Sprache + prominenter HUD-Button) bricht jede laufende Aktion ab.
- **Prompt-Injection-Grenze:** Inhalte aus Web, E-Mail und Dateien sind Daten, keine Anweisungen. Sie können die Risikostufe einer Aktion niemals senken.

### Datenschutz (Anforderung 16) — mit iPad-Besonderheiten

- **Audio wird nirgends persistiert.** Auf dem iPad nur ein RAM-Ringpuffer im AudioWorklet, auf dem Host wird der Stream direkt zum STT-Provider durchgereicht und verworfen. Aufzeichnung ausschließlich mit explizitem Debug-Flag, sichtbarem UI-Indikator und Auto-Löschung.
- **Mikrofon-Kontrolle:** Safari zeigt den Mikrofonzugriff systemseitig an; zusätzlich harter Mute-Schalter und permanenter Status im HUD. Im Idle *ohne* aktive Sitzung wird `getUserMedia` freigegeben — das Mikrofon ist dann geschlossen, nicht nur stumm.
- **Transportverschlüsselung ist Pflicht, nicht optional.** `getUserMedia` funktioniert in Safari ohnehin nur unter HTTPS/localhost. Über Tailscale kommt TLS mit; im LAN ein lokales Zertifikat (mkcert/Caddy).
- **Secrets** ausschließlich auf dem **Host** in Umgebungsvariablen / OS-Keychain. **Niemals im iPad-Client** — der Client hat nur ein Session-Token, keine Provider-Keys. `.env` in `.gitignore`, Pre-Commit-Secret-Scanner.
- **Logging:** strukturiert (JSON) mit Redaction-Layer. Transkripte und Antworten standardmäßig nur gekürzt/gehasht; Volltext nur im Debug-Modus.
- **Datenabfluss-Transparenz:** `docs/data-flows.md` listet pro Provider auf, *was* übertragen wird (Rohaudio? Transkript? Kontext? Memory?), wohin und mit welcher Retention. Im gewählten Cloud-first-Modus gilt explizit: **rohes Mikrofon-Audio verlässt über den Host den eigenen Bereich** (Deepgram/ElevenLabs). Das ist bewusst entschieden.
- **Umschaltbarer Datenschutzmodus:** Ein Schalter im HUD wechselt auf faster-whisper + Ollama + Piper auf dem Host. Dann verlässt kein Byte dein Netz. Qualität geringer, aber First-Class-Betriebsmodus, kein Notbehelf.

---

## 13. Interface-Architektur (HUD auf dem iPad)

Beide Interfaces — Sprache und Text — sprechen denselben Core über dasselbe Protokoll. Es gibt keinen zweiten JARVIS.

**Protokoll (Auszug; typisiert in `protocol.py`, nach `protocol.ts` generiert):**

| Richtung | Event | Nutzlast |
|---|---|---|
| Host → iPad | `state.changed` | `idle` / `listening` / `thinking` / `speaking` |
| Host → iPad | `transcript.partial` / `.final` | Text, Confidence |
| Host → iPad | `text.delta` | Antwort-Token (Sprechblase tippt mit) |
| Host → iPad | `audio.chunk` *(binär)* | Opus/PCM-Frame + Sequenznummer |
| Host → iPad | `tool.started` / `.finished` | Tool, Args (redigiert), Ergebnis |
| Host → iPad | `agent.status`, `plan.updated`, `memory.updated`, `system.metrics` | … |
| Host → iPad | `confirm.request` | Aktion, Risikostufe, Klartextbeschreibung |
| Host → iPad | `browser.frame` *(binär)* | Screenshot des Browser-Agenten |
| iPad → Host | `audio.frame` *(binär)* | 20-ms-PCM-Frame |
| iPad → Host | `user.text`, `user.interrupt`, `confirm.response`, `mic.toggle`, `settings.update` | … |

Der Audiopegel wird **nicht** übertragen — der Client misst ihn lokal am `AnalyserNode` des tatsächlich abgespielten Signals. Das spart Bandbreite und ist exakt synchron zum Gehörten.

**HUD-Umsetzung nach dem Screenshot:**
- Layout: linke Sidebar (Navigation, Voice-Status, Focus Mode), zentraler Orb mit Orbit-Ringen, rechte Spalte (Live Intelligence Feed, Quick Commands), untere Reihe (System Monitor, Memory Insights, LLM Status), Statusleiste unten.
- Farbwelt: dunkles Navy `#0a1628`, Cyan-Akzent `#22d3ee`, Glow über `box-shadow` + `backdrop-blur`.
- **iPad-spezifisch:** Landscape als Primärlayout (das Grid braucht Breite), Portrait als gestapelte Variante. Alle Ziele ≥ 44 pt (Apple HIG). `viewport-fit=cover` + Safe-Area-Insets. `user-select: none` und `touch-action` gesetzt, damit keine Textauswahl-Lupe beim Halten erscheint. Kein `:hover`-abhängiges UI.
- **CoreOrb:** Canvas-Renderer; Rotationsgeschwindigkeit und Glow an den Zustand gekoppelt — idle langsam pulsierend, listening reaktiv auf Eingangspegel, thinking schnelle Orbits, speaking an den Ausgabepegel gekoppelt.

**Sprechblase (explizit gefordert):**
- Erscheint bei `state=speaking`, am Orb verankert, Spring-Animation über Framer Motion.
- Der Text erscheint **synchron zum gesprochenen Wort**, nicht als fertiger Block: `text.delta` füllt einen Puffer, ausgegeben wird gedrosselt auf die tatsächliche Wiedergabeposition des AudioWorklet-Players. Das ist der Unterschied zwischen „wirkt lebendig" und „wirkt wie ein Untertitel, der zu früh kommt".
- Bei Barge-in kollabiert sie sofort; der bereits gesprochene Teil bleibt im Verlauf stehen, der Rest wird verworfen.

**Visuelle Haptik (explizit gefordert — und auf dem iPad die einzig mögliche Form):**
- `VoiceVisualizer`: `AnalyserNode` auf dem abgespielten Stream → Frequenzbänder → Wellenform/Balken um den Orb, 60 fps über `requestAnimationFrame`.
- `HapticPulse`: konzentrische Ringe, die im Rhythmus der Sprachenergie nach außen laufen; Amplitude aus dem RMS-Pegel, bei Energiespitzen (Betonungen) ein stärkerer Puls. Beim Zuhören läuft derselbe Effekt invertiert nach innen — so ist auf einen Blick erkennbar, wer gerade spricht.
- Ergänzend: kurze Skalierungs-/Glow-Impulse bei Turn-Start, Turn-Ende und Bestätigungsdialogen als taktil *wirkendes* Feedback.
- `prefers-reduced-motion` wird respektiert (statische Pegelanzeige statt Animation).
- Hinweis: `navigator.vibrate` existiert auf iPadOS nicht und iPads haben keine Vibrationshardware — echte Haptik ist plattformseitig ausgeschlossen. Falls JARVIS später auch auf dem iPhone laufen soll, wird `navigator.vibrate` bzw. Core Haptics über Capacitor als optionale Erweiterung ergänzt; die Abstraktion (`HapticEngine` mit No-op-Implementierung) wird jetzt schon vorgesehen.

**Text-Interface:** dieselbe WS-Verbindung, `user.text` statt Audioframes. Zusätzlich eine schlanke CLI auf dem Host (`jarvis chat`) für Entwicklung und Headless-Betrieb.

---

## 14. Entwicklungsphasen

| Phase | Umfang | Definition of Done |
|---|---|---|
| **1. Architektur** | *dieses Dokument* | Freigabe durch dich |
| **2. Minimal Viable JARVIS** | Host: Repo-Setup, Config, Event-Bus, `JarvisCore`, WS-Server + Auth, je 1× STT/TTS/LLM hinter den ABCs, Barge-in-Gegenstück. iPad: minimaler Client (Mic-Capture, AudioWorklet-Player, Silero-VAD, „Tap to Speak", Statusanzeige, Roh-Transkript) — **bewusst hässlich**, kein HUD. | Flüssiges deutsches Sprachgespräch vom iPad über mehrere Turns mit Kontextbezug; Reinsprechen stoppt die Ausgabe in < 300 ms; kein hörbares Echo; Latenzen protokolliert |
| **3. Tools** | `ToolRegistry`, `Tool`-ABC, Risikostufen, Bestätigungsfluss (inkl. Dialog auf dem iPad), Browser-Tool (Playwright auf dem Host), erste Builtins | „Öffne diese Seite und finde die Preise" funktioniert Ende zu Ende; eine SENSITIVE-Aktion löst nachweislich einen Bestätigungsdialog aus |
| **4. Research + Perplexity** | `ResearchProvider`-Abstraktion, Perplexity Sonar, Websuche, Fetcher/Extractor/Evaluator | Mehrquellen-Recherche mit Zitaten; ein widersprüchliches Thema wird als widersprüchlich benannt |
| **5. Planner + Agenten** | Planner, Agent-ABC + Registry, Conversation/Research/Browser-Agent, Schritt- und Kostenbudgets | Mehrschrittige Aufträge werden zerlegt und delegiert; Plan ist im Client sichtbar |
| **6. Memory** | SQLite + sqlite-vec, Short/Working/Long-Term, Hybrid-Retrieval, Extraktions-Gate | JARVIS erinnert sich sitzungsübergreifend; Einträge sind einsehbar und löschbar |
| **7. HUD + Erweiterungen** | Vollständiges HUD nach Screenshot, Sprechblase, visuelle Haptik, PWA-Manifest/Icons, Wake Word (Vordergrund), optional Capacitor-Hülle, MCP-Client, Integrationen | Der Screenshot ist real und funktional auf dem iPad |

Nach jeder Phase: lauffähiger Stand, Tests grün, kurzer Abnahme-Check mit dir. Kein „Big Bang".

**Zusätzlicher Aufwand durch Phase 2 auf dem iPad** gegenüber Desktop: Audio-I/O muss in TypeScript statt Python gebaut werden (AudioWorklets, Resampling, VAD in WASM), plus HTTPS-Setup. Grob ein bis zwei zusätzliche Arbeitstage — dafür entfällt die AEC-Eigenentwicklung, die deutlich teurer gewesen wäre.

---

## 15. Abhängigkeiten

**Host (Python)**
```
Kern:      fastapi, uvicorn[standard], websockets, pydantic, pydantic-settings,
           structlog, httpx, anyio
Audio:     numpy, soxr (Resampling), onnxruntime (Silero VAD, nur Headless-Tests),
           pyogg / opuslib (Opus-Transcodierung)
STT:       deepgram-sdk | elevenlabs | faster-whisper
TTS:       elevenlabs | cartesia | piper-tts
LLM:       anthropic (primär), openai, google-genai, ollama
Browser:   playwright
Research:  trafilatura, beautifulsoup4
Memory:    sqlalchemy, sqlite-vec, sentence-transformers
Dev:       pytest, pytest-asyncio, ruff, mypy, pre-commit, detect-secrets
```

**iPad-Client (npm)**
```
react, react-dom, typescript, vite, tailwindcss, framer-motion,
zustand, lucide-react, recharts,
onnxruntime-web            (Silero VAD, später openWakeWord)
vite-plugin-pwa            (Manifest, Service Worker, Home-Bildschirm)
@capacitor/core, @capacitor/ios   (optional, ab Phase 7)
```

**System:** Host mit Python 3.12+, Node 20+, Chromium via `playwright install`, TLS-Zertifikat (Caddy/mkcert) oder Tailscale. iPad mit iPadOS 17+ (Safari 17+ für AudioWorklet + `onnxruntime-web` SIMD).

---

## 16. Kosten (realistische Schätzung)

Annahme „normale persönliche Nutzung": ~50 Sprach-Turns/Tag, ~30 s Audio ein und ~20 s Audio aus pro Turn, ~25 Tage/Monat.

| Posten | Menge/Monat | Preis | Kosten/Monat |
|---|---|---|---|
| STT (Deepgram/ElevenLabs Streaming) | ~10 h | ~$0,30–0,45/h | **~$3–5** |
| TTS (ElevenLabs Flash / Cartesia) | ~600k Zeichen | ~$0,03–0,05/1k Z. | **~$18–30** |
| LLM Konversation (Sonnet 5, mit Prompt-Caching) | ~1250 Turns | ~$0,01–0,03/Turn | **~$15–35** |
| LLM Planer/Agenten (Opus 5, selektiv) | ~150 Aufgaben | ~$0,05–0,20/Aufgabe | **~$10–30** |
| Perplexity Sonar | ~200 Suchen | $1/Mio. Token | **~$2–5** |
| Websuche (Brave/Tavily) | ~500 Anfragen | Free-Tier bis ~2k | **~$0–5** |
| **Summe Cloud** | | | **≈ $50–110 / Monat** |
| Host-Hardware (falls neu) | einmalig | Mac mini / NUC / Pi 5 | **~$100–700 einmalig** |
| Host-Strom | Dauerbetrieb | ~5–15 W | **~$1–4 / Monat** |
| Tailscale | Personal | kostenlos | **$0** |
| **Datenschutzmodus** (Whisper + Ollama + Piper auf dem Host) | | Strom + GPU-Hardware | **≈ $0 laufend** |

Kostenhebel, von Anfang an eingebaut: Prompt-Caching (System-Prompt und Tool-Schemas sind stabil → bis zu 90 % Ersparnis auf dem gecachten Anteil), Modell-Routing über den Router, `effort`-Regler, Textmodus umgeht TTS komplett, harte Monats-Budgetgrenze mit Warnung im HUD, Kostenanzeige pro Turn.

---

## 17. Risiken und Alternativen

| # | Risiko | Auswirkung | Gegenmaßnahme / Alternative |
|---|---|---|---|
| 1 | **Host muss laufen** — iPad allein ist nutzlos | JARVIS ist offline, wenn der Host aus/nicht erreichbar ist | Bewusste Konsequenz der Plattformwahl. Host als Always-on-Gerät + Tailscale. HUD zeigt Verbindungsstatus prominent und bietet Offline-Ansicht der letzten Konversation. Alternative wäre eine native Swift-App ohne Playwright und ohne Provider-Austauschbarkeit — architektonisch deutlich schlechter. |
| 2 | **Latenz-Kette** — STT + LLM + TTS + Netz summieren sich | Gespräch fühlt sich tot an | Alles streamen, TTS ab erster Satzgrenze, kleines Modell für einfache Turns. Netz-Hop ist mit 1–60 ms der kleinste Posten. **Alternative:** Speech-to-Speech-Modell (OpenAI Realtime / Gemini Live) statt Pipeline — geringere Latenz, aber weniger Tool-Kontrolle und keine Provider-Austauschbarkeit; deshalb nicht Default, aber als vierter Providertyp architektonisch offengehalten. |
| 3 | **Safari-Audio-Eigenheiten** — AudioContext muss per Nutzergeste entsperrt werden; Ausgabe pausiert bei App-Wechsel oder Bildschirmsperre | Sprachausgabe bricht scheinbar grundlos ab | „Tap to Speak" entsperrt den AudioContext beim ersten Tap (passt zum Screenshot). `visibilitychange`-Handler pausiert die Session sauber und zeigt das an, statt still zu scheitern. Capacitor-Hülle mit Background-Audio-Modus, falls es stört. |
| 4 | **Kein Immer-an-Wake-Word auf iPadOS** | „Hey JARVIS" funktioniert nur bei offener App | Von vornherein so kommuniziert. Primär bleibt „Tap to Speak"/Push-to-Talk. Wake Word ab Phase 7 im Vordergrund. Wer echtes Always-on will, betreibt zusätzlich ein Mikrofon am Host — dieselbe Pipeline, anderer Audio-Client. |
| 5 | **Deutsche STT-Qualität** über iPad-Mikrofon nach AEC | Frustrierende Fehltranskriptionen | Provider-Benchmark mit *echten iPad-Aufnahmen* vor der Festlegung (nicht mit Studiomaterial). Custom-Vokabular/Keyword-Boosting. Provider bleibt austauschbar. |
| 6 | **Prompt-Injection** über Webseiten/E-Mails | Agent führt fremde Anweisungen aus | Untrusted-Content-Markierung, Risikostufen durch Inhalte nie senkbar, Domain-Allowlist, Bestätigung ab SENSITIVE. |
| 7 | **Offener Core im Netz** — der Host kann Mails senden und Browser steuern | Sehr hoher Schaden bei Kompromittierung | Kein Port-Forwarding. Tailscale + Token-Auth + TLS. Kein Provider-Key verlässt jemals den Host. Rate-Limit auf dem WS-Endpunkt. |
| 8 | **Kostenexplosion** durch schleifende Agenten | Unerwartete Rechnung | Schritt- und Kostenbudgets pro Aufgabe, harte Monatsgrenze, Kostenanzeige pro Turn. |
| 9 | **Over-Engineering** — 13 Module vor dem ersten funktionierenden Gespräch | Projekt stirbt vor Phase 2 | Phasenplan ist bindend: Phase 2 baut *nur* die Sprachschleife, UI bewusst hässlich. Alle anderen Module existieren zunächst als ABC + Fake-Implementierung. |
| 10 | **Provider-Lock-in** trotz Abstraktion | Wechsel wird teuer | Anbieterspezifische Features nur über `Capabilities` abgefragt, nie vorausgesetzt; Fake-Provider in Tests erzwingt Interface-Disziplin. |
| 11 | **Memory wird zu Rauschen** | Retrieval liefert Müll, Antworten verschlechtern sich | Extraktions-Gate vor jedem Write, Dedup, Sichtbarkeit und Löschbarkeit im HUD, Confidence-/Recency-Decay. |
| 12 | **Playwright-Brüchigkeit** bei Layout-Änderungen | Browser-Tool versagt sporadisch | Accessibility-Tree + beschreibungsbasierte Element-Auflösung statt CSS-Selektoren; Screenshot + Vision-Modell als Fallback. |

**Bewusst verworfene Alternativen:**
- *Native iPadOS-App in Swift mit lokalem Core* — kein Playwright, kein Python-ML-Ökosystem, jeder Provider müsste neu implementiert werden. Widerspricht der Kernanforderung Modularität.
- *Node/TypeScript für den Core* — schlechtere ML-/Browser-Bindings, hätte Python-Subprozesse erzwungen.
- *LangChain/LlamaIndex als Framework* — für einen persönlichen Assistenten mehr Abstraktions-Overhead als Nutzen; gefordert ist eine eigene, kontrollierte Architektur.
- *`<audio>`-Element für die Wiedergabe* — nach `pause()` spielt der Puffer weiter, Barge-in wäre unmöglich. Deshalb AudioWorklet.
- *Audiopegel vom Host an den Client senden* — Bandbreitenverschwendung und immer leicht asynchron zum Gehörten. Client misst lokal.
- *Browser-Automation für Perplexity* — unnötig, offizielle API vorhanden; von dir ausgeschlossen.
- *Reine Vektorsuche im Memory* — versagt bei Eigennamen; deshalb Hybrid.

---

## Verifikation je Phase

- **Phase 2:** Vom iPad aus ein 5-Turn-Gespräch auf Deutsch mit Kontextbezug. Mitten in einer Antwort hineinsprechen → Ausgabe stoppt in < 300 ms, kein Nachlaufen des Puffers. Über Lautsprecher (nicht nur Kopfhörer) prüfen, dass JARVIS sich nicht selbst unterbricht. Latenzen (Sprachende → erster Ton) protokolliert und im Log auswertbar.
- **Phase 3:** „Öffne example.com und lies mir die Überschriften vor" funktioniert Ende zu Ende; eine SENSITIVE-Aktion erzeugt nachweislich einen Bestätigungsdialog auf dem iPad, Timeout führt zu Ablehnung.
- **Phase 4:** Research-Anfrage liefert ≥ 3 Quellen mit URLs; ein bewusst widersprüchliches Thema wird als widersprüchlich benannt.
- **Phase 5:** Mehrschritt-Auftrag erzeugt einen sichtbaren Plan; Agenten-Delegation im Event-Log nachvollziehbar; Budget-Abbruch wird sauber gemeldet.
- **Phase 6:** Fakt in Sitzung A nennen, Host-Prozess neu starten, in Sitzung B abfragen → korrekt erinnert; Eintrag im Memory-Panel löschbar.
- **Phase 7:** HUD auf dem iPad entspricht dem Screenshot in Landscape und Portrait; Sprechblase erscheint synchron zur Sprachausgabe; Haptik-Puls folgt dem Audiopegel; als PWA vom Home-Bildschirm startbar.
- **Durchgehend:** `pytest` grün ohne API-Keys und ohne Mikrofon (Fake-Provider), `ruff` + `mypy` sauber, `tsc --noEmit` sauber, Secret-Scanner im Pre-Commit.

---

## Ein offener Punkt (blockiert Phase 2 nicht)

**Auf welchem Gerät soll der Host laufen?** Empfehlung: ein vorhandener oder günstiger Always-on-Rechner im eigenen Netz (Mac mini, NUC, Raspberry Pi 5 — für den Cloud-Modus genügt schwache Hardware, da die schwere Arbeit bei den Providern liegt) plus Tailscale für den Zugriff unterwegs. Für den späteren Datenschutzmodus mit lokalen Modellen wäre eine GPU oder ein Apple-Silicon-Mac sinnvoll.

Ich beginne Phase 2 mit einem Host-Setup, das auf macOS und Linux gleichermaßen läuft, und einer Konfiguration, die die Host-Adresse zur Laufzeit setzt — die Entscheidung lässt sich also jederzeit nachziehen.

---

# Phase 2 — Konkreter Umsetzungsplan

**Freigegeben:** Architektur (Phase 1) ist von dir abgenommen. Kostenmodell geklärt: reine Pay-per-Use-Abrechnung, kein Grundpreis; einziger Dauerposten ist der Host-Strom.

**Ziel dieser Phase:** Ein flüssiges deutsches Sprachgespräch vom iPad zum Host — mehrere Turns mit Kontextbezug, unterbrechbar. Kein HUD, keine Tools, kein Memory über die Session hinaus. Die UI ist bewusst hässlich.

## Was gebaut wird

**Repo-Grundgerüst**
- `pyproject.toml` (uv, Python 3.12+), `.env.example`, `.gitignore`, `ruff`/`mypy`-Konfiguration, `.pre-commit-config.yaml` mit `detect-secrets`
- `config/jarvis.yaml` — Profil: Sprache `de`, Stimme, aktive Provider je Slot, Barge-in-Schwellen
- `src/jarvis/config/settings.py` — `pydantic-settings`, Secrets ausschließlich aus Umgebung

**Core (`src/jarvis/core/`)**
- `events.py` — typisierter async Event-Bus (pub/sub), Grundlage für alles Spätere
- `session.py` — Session- und Turn-Lifecycle mit `CancelScope` pro Turn; ein Turn ist als Ganzes abbrechbar
- `kernel.py` — `JarvisCore`: nimmt ein finales Transkript entgegen, führt Konversationsverlauf, ruft `LLMProvider.stream()`, gibt Text-Deltas und Satzgrenzen weiter. Kennt ausschließlich die ABCs
- `errors.py`

**Provider-Abstraktionen mit je einer echten und einer Fake-Implementierung**
- `voice/stt/base.py` + `deepgram.py` + `fake.py`
- `voice/tts/base.py` + `elevenlabs.py` + `fake.py`
- `llm/base.py` + `anthropic.py` + `fake.py` (Prompt-Caching für den System-Prompt von Anfang an)
- Auswahl per Factory aus der Config; `router.py` zunächst als triviale Modellwahl (Sonnet für Konversation)

**Sprachschleife (`src/jarvis/voice/`)**
- `pipeline.py` — Audio-In → STT-Stream → Core → Satz-Chunker → TTS-Stream → Audio-Out, alles nebenläufig
- `audio_stream.py` — Frames vom/zum WS-Client, **kein lokales Audiogerät**
- `barge_in.py` — Host-Seite: auf `user.interrupt` sofort TTS `stop()`, LLM-Stream canceln, gesprochenes Text-Präfix ermitteln und nur dieses in den Verlauf schreiben
- Satz-Chunker: TTS startet an der ersten Satzgrenze, nicht erst nach der vollständigen LLM-Antwort — der größte Latenzhebel

**Transport (`src/jarvis/interfaces/`)**
- `protocol.py` — typisierte WS-Nachrichten nach der Tabelle in §13, Phase-2-Teilmenge: `state.changed`, `transcript.partial/.final`, `text.delta`, `audio.chunk`, `user.text`, `audio.frame`, `user.interrupt`, `mic.toggle`
- `server.py` — FastAPI + einzelner WS-Endpunkt, binäre Audioframes und JSON-Events auf demselben Kanal
- `auth.py` — vorgeteilter Token, Rate-Limit
- `cli.py` — `jarvis chat`, Textmodus ohne Audio, für Entwicklung und Headless-Tests

**iPad-Client (`ui/`) — bewusst minimal**
- Vite + React + TypeScript, kein Tailwind-Feinschliff, keine Animationen
- `lib/audio/capture.ts` — `getUserMedia` mit `echoCancellation`/`noiseSuppression`/`autoGainControl`, AudioWorklet, Resampling auf 16 kHz mono
- `lib/audio/player.ts` — AudioWorklet-Player mit Chunk-Queue und `flush()` in einem Frame (**kein `<audio>`-Element**)
- `lib/audio/vad.ts` — Silero VAD als ONNX über `onnxruntime-web`, läuft durchgehend, auch während der Wiedergabe; ≥ 200 ms Sprache → lokaler Flush, dann `user.interrupt` an den Host
- `lib/ws.ts` — typisierter Client mit Reconnect und Backoff
- `App.tsx` — ein „Tap to Speak"-Button (entsperrt zugleich den AudioContext), Statustext, Roh-Transkript, Latenzanzeige. Mehr nicht.

**Tests (`tests/`)**
- pytest + pytest-asyncio, vollständig mit Fake-Providern: Event-Bus, Turn-Lifecycle, Satz-Chunker, Barge-in-Präfixlogik, Protokoll-Serialisierung, WS-Auth
- Muss ohne API-Keys und ohne Mikrofon grün sein

## Was ich hier verifizieren kann — und was nicht

In diesem Container laufen `pytest`, `ruff`, `mypy` und `tsc --noEmit` durch; die Sprachschleife wird end-to-end mit Fake-Providern getestet (synthetisches Audio rein, deterministischer Text raus, simulierter Barge-in). **Nicht** prüfbar sind hier: echtes iPad-Mikrofon, Safari-AudioWorklet-Verhalten, AEC über Lautsprecher, echte Provider-Latenzen. Diese vier Punkte prüfst du auf dem Mac mini und dem iPad — ich liefere dazu eine `docs/phase2-abnahme.md` mit der konkreten Schrittfolge (Setup, Zertifikat/Tailscale, Keys eintragen, Messpunkte).

## Abnahmekriterien

- 5-Turn-Gespräch auf Deutsch vom iPad, mit Kontextbezug über die Turns hinweg
- Reinsprechen stoppt die Ausgabe in < 300 ms, kein Nachlaufen des Puffers
- Über Lautsprecher (nicht nur Kopfhörer): JARVIS unterbricht sich nicht selbst
- Latenz Sprachende → erster Ton wird pro Turn protokolliert
- `pytest`, `ruff`, `mypy`, `tsc --noEmit` grün ohne Keys

## Reihenfolge der Commits

1. Repo-Grundgerüst, Config, Linting, CI-taugliche Testbasis
2. Event-Bus, Session/Turn, `JarvisCore` mit Fake-LLM + Tests
3. Provider-ABCs + Fakes + Anthropic-Adapter mit Prompt-Caching
4. WS-Protokoll, Server, Auth, `jarvis chat`-CLI
5. Sprachschleife: STT/TTS-Adapter, Satz-Chunker, Barge-in-Host-Seite
6. iPad-Client: Capture, Player, VAD, WS, minimale UI
7. `docs/phase2-abnahme.md` + `docs/data-flows.md`

Entwicklung auf `claude/jarvis-ai-assistant-architecture-r5j65k`, Push nach jedem sinnvollen Abschnitt. Ein Pull Request wird erst geöffnet, wenn du ihn anforderst.
