/**
 * WebSocket-Client mit Auto-Reconnect.
 *
 * Verbindungsabbrüche sind auf einem Mobilgerät der Normalfall, nicht die
 * Ausnahme: Bildschirmsperre, WLAN-Wechsel, App im Hintergrund. Der Client
 * ist deshalb von Anfang an darauf ausgelegt, still wieder hochzukommen —
 * und den Zustand sichtbar zu machen, statt stumm zu scheitern.
 */

import type { ClientMessage, ServerMessage } from './protocol';
import { decodeAudioFrame, type DecodedAudioFrame } from './protocol';

export type ConnectionState = 'connecting' | 'open' | 'closed';

export interface JarvisSocketHandlers {
  onMessage: (message: ServerMessage) => void;
  onAudio: (frame: DecodedAudioFrame) => void;
  onConnectionState: (state: ConnectionState) => void;
}

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10_000;

export class JarvisSocket {
  private socket: WebSocket | null = null;
  private attempt = 0;
  private closedByUser = false;
  private timer: number | null = null;

  constructor(
    private readonly url: string,
    private readonly handlers: JarvisSocketHandlers,
  ) {}

  connect(): void {
    this.closedByUser = false;
    this.open();
  }

  private open(): void {
    this.handlers.onConnectionState('connecting');
    const socket = new WebSocket(this.url);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.handlers.onConnectionState('open');
    };

    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        try {
          this.handlers.onAudio(decodeAudioFrame(event.data));
        } catch (error) {
          console.warn('Audioframe verworfen:', error);
        }
        return;
      }
      try {
        this.handlers.onMessage(JSON.parse(event.data as string) as ServerMessage);
      } catch (error) {
        console.warn('Nachricht verworfen:', error);
      }
    };

    socket.onclose = () => {
      this.socket = null;
      this.handlers.onConnectionState('closed');
      if (!this.closedByUser) this.scheduleReconnect();
    };

    socket.onerror = () => socket.close();
  }

  private scheduleReconnect(): void {
    if (this.timer !== null) return;
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this.attempt, RECONNECT_MAX_MS);
    this.attempt += 1;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      if (!this.closedByUser) this.open();
    }, delay);
  }

  get isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  send(message: ClientMessage): void {
    if (this.isOpen) this.socket!.send(JSON.stringify(message));
  }

  /** Mikrofonframe hochschieben. Wird 50-mal pro Sekunde gerufen. */
  sendAudio(frame: ArrayBuffer): void {
    if (this.isOpen) this.socket!.send(frame);
  }

  close(): void {
    this.closedByUser = true;
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    this.socket?.close();
  }
}
