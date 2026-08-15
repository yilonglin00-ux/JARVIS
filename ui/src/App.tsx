/**
 * Phase-2-Oberfläche: bewusst hässlich.
 *
 * Hier wird nichts gestaltet. Diese Ansicht existiert, um die Sprachschleife
 * beurteilen zu können — Zustand, Transkript, Antwort, Latenz, Ereignislog.
 * Das HUD aus dem Entwurf kommt in Phase 7 und ersetzt genau diese Datei,
 * ohne dass an `lib/` etwas geändert werden muss.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { initialView, JarvisClient, type JarvisView } from './lib/jarvis';

function defaultUrl(): string {
  const stored = localStorage.getItem('jarvis.url');
  if (stored) return stored;
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.hostname}:8765/ws?token=`;
}

export default function App() {
  const [view, setView] = useState<JarvisView>(initialView);
  const [url, setUrl] = useState(defaultUrl);
  const [draft, setDraft] = useState('');
  const clientRef = useRef<JarvisClient | null>(null);

  const client = useMemo(() => {
    const instance = new JarvisClient(setView);
    clientRef.current = instance;
    return instance;
  }, []);

  useEffect(() => () => clientRef.current?.disconnect(), []);

  const connect = useCallback(async () => {
    localStorage.setItem('jarvis.url', url);
    try {
      await client.connect(url);
    } catch (error) {
      setView((current) => ({ ...current, error: String(error) }));
    }
  }, [client, url]);

  const toggleMic = useCallback(async () => {
    try {
      if (view.micOpen) client.stopMic();
      else await client.startMic();
    } catch (error) {
      setView((current) => ({ ...current, error: String(error) }));
    }
  }, [client, view.micOpen]);

  const submitText = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      if (!draft.trim()) return;
      client.sendText(draft.trim());
      setDraft('');
    },
    [client, draft],
  );

  return (
    <main>
      <h1>JARVIS — Phase 2</h1>

      <section>
        <label htmlFor="url">Host</label>
        <input
          id="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="wss://host:8765/ws?token=…"
          autoComplete="off"
          spellCheck={false}
        />
        <button type="button" onClick={connect} disabled={view.connection === 'open'}>
          Verbinden
        </button>
      </section>

      <section className="status">
        <span>Verbindung: {view.connection}</span>
        <span>Zustand: {view.state}</span>
        <span>Mikrofon: {view.micOpen ? 'offen' : 'zu'}</span>
        <span>VAD: {view.vadKind}</span>
        <span>
          Latenz: {view.lastLatencyMs === null ? '—' : `${view.lastLatencyMs} ms`}
        </span>
      </section>

      <section className="controls">
        {/* Ein Tap entsperrt den AudioContext und öffnet das Mikrofon —
            auf iPadOS geht es technisch nicht ohne Nutzergeste. */}
        <button
          type="button"
          onClick={toggleMic}
          disabled={view.connection !== 'open'}
          className="primary"
        >
          {view.micOpen ? 'Mikrofon schließen' : 'Tap to Speak'}
        </button>
        <button
          type="button"
          onClick={() => client.interrupt()}
          disabled={view.state !== 'speaking'}
        >
          Stopp
        </button>
      </section>

      <form onSubmit={submitText}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Text statt Sprache…"
          disabled={view.connection !== 'open'}
        />
        <button type="submit" disabled={view.connection !== 'open'}>
          Senden
        </button>
      </form>

      {view.error && <p className="error">{view.error}</p>}

      <section>
        <h2>Transkript</h2>
        <p className="transcript">
          {view.transcript || '—'}
          {view.partial && <em> {view.partial}</em>}
        </p>
      </section>

      <section>
        <h2>Antwort</h2>
        <p className="reply">{view.reply || '—'}</p>
      </section>

      <section>
        <h2>Ereignisse</h2>
        <ul className="log">
          {view.log.map((line, index) => (
            <li key={`${line}-${index}`}>{line}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}
