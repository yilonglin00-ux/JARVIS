# Phase 2 — Abnahme auf echter Hardware

Was in diesem Repo mit Fakes getestet ist, sagt nichts über Echo, Mikrofonqualität
und echte Provider-Latenz aus. Diese vier Dinge lassen sich nur auf dem Mac mini
und dem iPad prüfen. Hier steht, wie.

Rechne mit gut einer Stunde beim ersten Mal, davon der größte Teil für TLS.

---

## 1. Host einrichten

```bash
git clone <repo> && cd jarvis
uv venv --python 3.12
uv pip install -e ".[dev,providers]"
cp .env.example .env
```

Token erzeugen und in `.env` unter `JARVIS_AUTH_TOKEN` eintragen:

```bash
.venv/bin/python -m jarvis.interfaces.cli token
```

API-Keys in `.env` eintragen: `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`,
`ELEVENLABS_API_KEY`. Dann prüfen:

```bash
.venv/bin/python -m jarvis.interfaces.cli doctor
```

**Stimme wählen.** In `config/jarvis.yaml` unter `tts.elevenlabs.voice_id` eine
deutsche Stimme aus deinem ElevenLabs-Konto eintragen. Bleibt das Feld leer,
nimmt der Adapter eine englischsprachige Standardstimme — verständlich, aber
mit hörbarem Akzent.

Bevor irgendetwas mit Audio läuft, den Weg ohne Mikrofon prüfen:

```bash
.venv/bin/python -m jarvis.interfaces.cli chat
```

Antwortet JARVIS hier sinnvoll auf Deutsch, sind LLM-Anbindung, Persona und
Verlauf in Ordnung. Erst dann weiter.

---

## 2. TLS — der einzige unangenehme Schritt

Safari gibt `getUserMedia` nur unter HTTPS oder auf `localhost` frei. Ohne
Zertifikat bleibt das Mikrofon auf dem iPad stumm, ohne brauchbare Fehlermeldung.

**Empfohlen: Tailscale.** Bringt TLS mit, funktioniert auch unterwegs, kein
Port-Forwarding im Router.

```bash
# auf dem Host
tailscale up
tailscale cert <hostname>.<tailnet>.ts.net   # legt Zertifikat und Key ab
```

Dann Host und Client hinter einem Reverse Proxy zusammenführen (Caddyfile):

```
<hostname>.<tailnet>.ts.net {
    tls /pfad/zum/cert.crt /pfad/zum/cert.key
    handle /ws*   { reverse_proxy 127.0.0.1:8765 }
    handle        { reverse_proxy 127.0.0.1:5173 }
}
```

Auf dem iPad die Tailscale-App installieren und mit demselben Konto anmelden.

**Alternative im LAN: mkcert.** Zertifikat für die lokale IP erzeugen und das
Root-CA auf dem iPad installieren (Einstellungen → Profil geladen → installieren
→ **und danach unter Info → Zertifikatsvertrauenseinstellungen aktivieren**).
Dieser letzte Schritt wird fast immer vergessen; ohne ihn verweigert Safari die
Verbindung weiterhin.

---

## 3. Silero-VAD-Modell (empfohlen, nicht zwingend)

Fehlt das Modell, läuft die energiebasierte Erkennung. Die funktioniert, hält
aber lautes Rascheln für Sprache und löst dadurch falsche Unterbrechungen aus.

```bash
mkdir -p ui/public/models
curl -L -o ui/public/models/silero_vad.onnx \
  https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx
```

Ob es geklappt hat, steht im Client in der Statuszeile: `VAD: silero` gegenüber
`VAD: energy`.

---

## 4. Starten

```bash
# Terminal 1
.venv/bin/python -m jarvis.interfaces.cli serve --host 0.0.0.0

# Terminal 2
cd ui && npm install && npm run dev
```

Auf dem iPad die HTTPS-Adresse öffnen, Token an die URL im Feld „Host" anhängen
(`wss://…/ws?token=DEIN_TOKEN`), **Verbinden**, dann **Tap to Speak** — dieser
Tap entsperrt zugleich den AudioContext, ohne ihn bleibt es still.

---

## 5. Die fünf Prüfungen

### 5.1 Gespräch über fünf Turns
Fünf Fragen hintereinander stellen, davon mindestens zwei mit Rückbezug
(„Und wie war das nochmal?"). JARVIS muss den Bezug halten.
**Bestanden, wenn** alle fünf Turns sinnvoll beantwortet werden und der Bezug sitzt.

### 5.2 Barge-in
Mitten in einer längeren Antwort dazwischenreden.
**Bestanden, wenn** die Ausgabe in unter 300 ms verstummt und **nichts
nachläuft**. Nachlaufender Puffer ist das typische Fehlerbild und wäre ein Fehler
im Player-Worklet, nicht in der Netzstrecke.

### 5.3 Kein Selbst-Unterbrechen — über Lautsprecher, nicht über Kopfhörer
Das ist die wichtigste Prüfung, und mit Kopfhörern ist sie wertlos: Nur über den
Lautsprecher hört das Mikrofon die eigene Ausgabe.
**Bestanden, wenn** JARVIS eine lange Antwort ohne Kopfhörer zu Ende spricht,
ohne sich selbst zu stoppen. Fällt der Test durch, zuerst in `capture.ts` prüfen,
ob `echoCancellation` wirklich aktiv ist (`getAudioTracks()[0].getSettings()`).

### 5.4 Backchannel wird ignoriert
Während JARVIS spricht kurz „mhm" einwerfen.
**Bestanden, wenn** er weiterspricht. Stoppt er, ist `barge_in_min_speech_ms` in
`config/jarvis.yaml` zu niedrig (Ausgangswert 200 ms, 300 ms probieren).

### 5.5 Latenz
Die Statuszeile zeigt „Latenz Sprachende → erster Ton" pro Turn; dieselben Werte
stehen im Host-Log unter `latency.speech_end_to_first_audio`.
**Richtwert:** unter 800 ms fühlt sich das Gespräch lebendig an, ab 1500 ms tot.

Über zehn Turns mitschreiben:

```bash
.venv/bin/python -m jarvis.interfaces.cli serve 2>&1 | grep latency
```

---

## 6. Wenn etwas nicht geht

| Symptom | Wahrscheinliche Ursache |
|---|---|
| Mikrofon-Dialog erscheint nicht | Kein HTTPS. Safari gibt `getUserMedia` sonst nicht frei. |
| Verbunden, aber kein Ton | AudioContext nicht entsperrt — es braucht einen echten Tap auf **Tap to Speak**. |
| Ton bricht beim App-Wechsel ab | Erwartetes Safari-Verhalten. Session neu starten; Abhilfe wäre die Capacitor-Hülle mit Background-Audio (Phase 7). |
| JARVIS unterbricht sich selbst | AEC greift nicht. Lautstärke senken, Abstand zum Mikrofon vergrößern, `echoCancellation` prüfen. |
| Ständige falsche Unterbrechungen | `VAD: energy` in der Statuszeile → Silero-Modell nachlegen (Schritt 3). |
| Transkript bricht mitten im Satz ab | `stt.deepgram.endpointing_ms` erhöhen (Ausgangswert 300). |
| Antwort kommt, aber stumm | Falsches `output_format`. Der Player kann nur rohes PCM, kein MP3 und kein Opus. |
| `1008` beim Verbinden | Token fehlt oder stimmt nicht. |

---

## 7. Was danach entschieden wird

Erst nach diesen Messungen — mit **echten iPad-Aufnahmen**, nicht mit
Studiomaterial — wird der STT-Provider festgelegt. Anbieter-Benchmarks
übertragen sich erfahrungsgemäß schlecht, und ein iPad-Mikrofon nach
WebRTC-Echounterdrückung klingt anders als alles, womit die Anbieter werben.

Zum Vergleichen `providers.stt` in `config/jarvis.yaml` umstellen und dieselben
fünf Prüfungen wiederholen. Mehr als eine Zeile Konfiguration kostet der Wechsel
nicht — genau dafür ist die Abstraktion da.
