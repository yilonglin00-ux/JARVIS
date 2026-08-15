# Datenflüsse

Wer ein Mikrofon in die Wohnung stellt, hat ein Recht darauf, genau zu wissen,
was wohin geht. Diese Datei listet das pro Provider auf und wird bei jedem
neuen Provider mitgepflegt.

## Phase 2 und 3 — was heute existiert

| Weg | Inhalt | Empfänger | Verschlüsselt | Gespeichert |
|---|---|---|---|---|
| iPad → Host | Rohes Mikrofon-PCM, 16 kHz mono, 20-ms-Frames | eigener Host | ja (wss) | **nein** — nur RAM-Puffer, wird nach Weitergabe verworfen |
| Host → Deepgram | Dasselbe Rohaudio | Deepgram (USA) | ja (wss) | nach Anbieterrichtlinie; Opt-out für Modelltraining im Konto prüfen |
| Deepgram → Host | Transkript (Partials + Finals) | eigener Host | ja | im RAM; ins Log nur gekürzt und gehasht |
| Host → Anthropic | System-Prompt, Gesprächsverlauf dieser Session, aktuelle Äußerung | Anthropic | ja (https) | nach Anbieterrichtlinie |
| Anthropic → Host | Antworttext | eigener Host | ja | im Gesprächsverlauf der laufenden Session (RAM) |
| Host → ElevenLabs | Antworttext, satzweise | ElevenLabs | ja (wss) | nach Anbieterrichtlinie |
| ElevenLabs → Host → iPad | Synthetisiertes PCM | eigener Host, dann iPad | ja | **nein** |

**Wichtigste Konsequenz des gewählten Cloud-Modus:** Rohes Mikrofon-Audio
verlässt über den Host dein Netz. Das ist eine bewusste Entscheidung für
Qualität und Latenz, keine Nebenwirkung.

### Dazu seit Phase 3 (Werkzeuge)

| Weg | Inhalt | Empfänger | Verschlüsselt | Gespeichert |
|---|---|---|---|---|
| Host → Anthropic | Zusätzlich: Werkzeugschemas, Werkzeugaufrufe und deren **Ergebnisse** | Anthropic | ja | nach Anbieterrichtlinie |
| Dateiwerkzeuge | Nur innerhalb `files.root` (Vorgabe `~/JARVIS-Dateien`) | bleibt lokal | — | ja, das ist ihr Zweck |
| Notizen | Klartext in `notizen.md` im selben Verzeichnis | bleibt lokal | — | ja, dauerhaft |
| Host → Webseite (Browser) | HTTP-Anfragen mit eigenem Browserprofil, nur an Domains aus `browser.allowed_domains` | die Webseite | je nach Seite | Cookies im eigenen Profil, getrennt vom Alltagsbrowser |
| Webseite → Host → Anthropic | Seitentext, als nicht vertrauenswürdig markiert | Anthropic | ja | nach Anbieterrichtlinie |
| Host → iPad | Werkzeugname, Risiko, **redigierte** Argumente | iPad | ja (wss) | nein |

**Der wichtigste neue Datenfluss:** Was ein Werkzeug liest, geht ans Modell —
also an Anthropic. Wer JARVIS eine Datei vorlesen lässt, schickt ihren Inhalt in
die Cloud. Der Sandkasten begrenzt, *welche* Dateien überhaupt in Frage kommen;
er verhindert nicht, dass ihr Inhalt den Host verlässt.

**Zwei Regeln zur Redaction, die sich unterscheiden:**

- Im **Log** und in `tool.started` werden Werte unter Schlüsseln wie
  `password`, `token` oder `api_key` durch `<verborgen>` ersetzt.
- In der **Rückfrage** stehen die echten Werte. Eine Bestätigung, die den
  Empfänger einer Mail verschweigt, wäre keine Bestätigung. Wer beurteilen
  soll, muss sehen.

## Was ausdrücklich nicht passiert

- **Kein Audio auf der Platte.** Weder auf dem iPad noch auf dem Host. Es gibt
  keinen Aufnahmemodus, der versehentlich anspringt.
- **Keine Provider-Keys im Client.** Das iPad kennt genau einen Wert: den
  Session-Token. Alle API-Schlüssel liegen auf dem Host in `.env` bzw. im
  OS-Keychain.
- **Keine Transkripte im Klartext im Log.** Der Redaction-Layer in
  `src/jarvis/logging.py` ersetzt Transkripte, Antworten und Nutzertext durch
  Länge plus Kurz-Hash. `JARVIS_LOG_TRANSCRIPTS=true` hebt das auf — der
  Schalter heißt so, wie er wirkt.
- **Kein offener Port ins Internet.** Der WS-Endpunkt verlangt einen Token und
  gehört hinter Tailscale oder ans lokale Interface gebunden. Kein
  Port-Forwarding im Router.
- **Kein Dateizugriff außerhalb des Sandkastens.** Auch nicht über `..`, nicht
  über absolute Pfade und nicht über Symlinks — geprüft wird der *aufgelöste*
  Pfad, nicht der angegebene.
- **Kein Browserzugriff ins eigene Netz.** Loopback, private IP-Bereiche und
  Link-Local sind gesperrt, selbst wenn `allowed_domains` auf `*` steht. Eine
  Wildcard ist eine Entscheidung über das Internet, nicht über deinen Router.

## Mikrofon-Kontrolle auf dem iPad

`MicCapture.stop()` ruft `stop()` auf jedem Track und gibt das Mikrofon damit
wirklich frei — die Systemanzeige von iPadOS erlischt. Bloßes Stummschalten
(`setMuted`) lässt das Mikrofon offen und ist als solches im Client sichtbar.
Diese Unterscheidung ist Absicht: eine Anzeige, die "aus" behauptet, während
das Mikrofon offen ist, wäre eine Vertrauenslüge.

## Datenschutzmodus (ab Phase 2 vorbereitet, Umsetzung später)

Ein Wechsel auf `faster-whisper` + `Ollama` + `Piper` auf dem Host — alle drei
hinter denselben Interfaces — bedeutet: **kein Byte verlässt dein Netz.**
Qualität und Latenz sind schlechter, aber es ist ein vollwertiger
Betriebsmodus, kein Notbehelf. Umschaltbar über `providers:` in
`config/jarvis.yaml`, ohne Codeänderung.

## Bei jedem neuen Provider zu beantworten

1. Was genau wird übertragen — Rohaudio, Transkript, Verlauf, Gedächtnis?
2. In welchem Land steht der Empfänger?
3. Wie lange speichert er, und lässt sich das abschalten?
4. Wird mit den Daten trainiert, und gibt es ein Opt-out?
5. Was passiert bei einem Ausfall — schweigt JARVIS oder fällt er zurück?
