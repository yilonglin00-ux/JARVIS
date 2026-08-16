/**
 * Die Sicherheitsrückfrage.
 *
 * Drei Dinge sind hier Absicht und keine Gestaltungsfrage:
 *
 * 1. **Die Frage steht im Klartext.** Der Host liefert einen konkreten
 *    Satz („Soll ich die Datei X überschreiben?"), nicht „Fortfahren?".
 * 2. **Bei `requires_tap` gibt es einen zweiten Schritt.** Bei
 *    zerstörenden Aktionen wird zuerst die Aktion wiederholt und erst der
 *    zweite Tap bestätigt. Ein einziger Knopf an derselben Stelle wie
 *    sonst „Ja" wäre genau der Reflex, den man hier nicht will.
 * 3. **Ablehnen ist die vorbelegte Antwort.** Escape, Wegtippen und
 *    Ablaufen der Zeit führen alle zum selben Ergebnis: es passiert nichts.
 */

import { useEffect, useState } from 'react';

import type { ConfirmRequestMsg, RiskLevel } from '../lib/protocol';

const RISK_LABEL: Record<RiskLevel, string> = {
  read: 'liest nur',
  low: 'kleine Änderung',
  sensitive: 'wirkt nach außen',
  destructive: 'zerstörend',
};

interface Props {
  request: ConfirmRequestMsg;
  onRespond: (requestId: string, approved: boolean) => void;
}

export function ConfirmDialog({ request, onRespond }: Props) {
  const [armed, setArmed] = useState(false);
  const [remaining, setRemaining] = useState(Math.round(request.timeout_s));

  // Neue Rückfrage: Zähler und Scharfstellung zurücksetzen, sonst würde
  // ein zweiter Dialog den bereits gedrückten Zustand des ersten erben.
  useEffect(() => {
    setArmed(false);
    setRemaining(Math.round(request.timeout_s));
  }, [request.request_id, request.timeout_s]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setRemaining((value) => (value > 0 ? value - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [request.request_id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onRespond(request.request_id, false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onRespond, request.request_id]);

  const entries = Object.entries(request.arguments);
  const needsSecondTap = request.requires_tap && !armed;

  return (
    <div className="confirm-backdrop" role="dialog" aria-modal="true">
      <div className={`confirm risk-${request.risk}`}>
        <p className="confirm-risk">
          {request.tool} · {RISK_LABEL[request.risk] ?? request.risk}
        </p>

        <p className="confirm-summary">{request.summary}</p>

        {entries.length > 0 && (
          <dl className="confirm-args">
            {entries.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
              </div>
            ))}
          </dl>
        )}

        {armed && (
          <p className="confirm-warning">
            Zum Ausführen ein zweites Mal bestätigen. Diese Aktion lässt sich nicht
            rückgängig machen.
          </p>
        )}

        <div className="confirm-actions">
          {/* Ablehnen steht links und ist der ruhige Knopf: Wer hastig
              tippt, soll nicht versehentlich zustimmen. */}
          <button type="button" onClick={() => onRespond(request.request_id, false)}>
            Nein
          </button>
          <button
            type="button"
            className="danger"
            onClick={() => (needsSecondTap ? setArmed(true) : onRespond(request.request_id, true))}
          >
            {needsSecondTap ? 'Ja, weiter' : 'Ja, ausführen'}
          </button>
        </div>

        <p className="confirm-timeout">
          Ohne Antwort passiert nichts{remaining > 0 ? ` (${remaining} s)` : ''}.
        </p>
      </div>
    </div>
  );
}
