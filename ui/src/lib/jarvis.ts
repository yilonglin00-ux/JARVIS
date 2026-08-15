/**
 * Klebeschicht zwischen Socket, Mikrofon, Wiedergabe und VAD.
 *
 * Hier steht der Barge-in-Pfad des Clients — der zeitkritischste Teil der
 * ganzen Anwendung. Reihenfolge bei einer erkannten Unterbrechung:
 *
 *   1. lokal flushen (Puffer leer in unter 3 ms)
 *   2. `user.interrupt` mit der tatsächlich gehörten Dauer senden
 *   3. Zustand auf "listening" setzen
 *
 * Erst flushen, dann melden. Andersherum würde man die Netzlaufzeit noch
 * weiterreden hören.
 */

import { MicCapture } from './audio/capture';
import { AudioPlayer } from './audio/player';
import { createVoiceDetector, SpeechGate, type VoiceDetector } from './audio/vad';
import type { ConversationState, ServerMessage } from './protocol';
import { JarvisSocket, type ConnectionState } from './ws';

export interface JarvisView {
  connection: ConnectionState;
  state: ConversationState;
  micOpen: boolean;
  transcript: string;
  partial: string;
  reply: string;
  lastLatencyMs: number | null;
  vadKind: string;
  error: string | null;
  log: string[];
}

const INITIAL: JarvisView = {
  connection: 'closed',
  state: 'idle',
  micOpen: false,
  transcript: '',
  partial: '',
  reply: '',
  lastLatencyMs: null,
  vadKind: '—',
  error: null,
  log: [],
};

export class JarvisClient {
  private socket: JarvisSocket | null = null;
  private context: AudioContext | null = null;
  private capture: MicCapture | null = null;
  private player: AudioPlayer | null = null;
  private detector: VoiceDetector | null = null;
  private gate: SpeechGate | null = null;

  private view: JarvisView = { ...INITIAL };
  private frameMs = 20;
  private sampleRate = 16000;
  private bargeInMinMs = 200;

  constructor(private readonly onChange: (view: JarvisView) => void) {}

  private patch(changes: Partial<JarvisView>): void {
    this.view = { ...this.view, ...changes };
    this.onChange(this.view);
  }

  private note(line: string): void {
    const stamped = `${new Date().toLocaleTimeString()}  ${line}`;
    this.patch({ log: [stamped, ...this.view.log].slice(0, 40) });
  }

  /**
   * Muss aus einer echten Nutzergeste heraus gerufen werden.
   *
   * Safari startet einen AudioContext nicht ohne Tap. Deshalb ist "Tap to
   * Speak" nicht nur eine Bedienidee, sondern technische Notwendigkeit
   * (Architektur §17, Risiko 3).
   */
  async connect(url: string): Promise<void> {
    if (!this.context) {
      this.context = new AudioContext();
    }
    if (this.context.state === 'suspended') {
      await this.context.resume();
    }

    if (!this.player) {
      this.player = new AudioPlayer(this.context);
      await this.player.start();
    }
    if (!this.detector) {
      this.detector = await createVoiceDetector();
      this.patch({ vadKind: this.detector.kind });
    }

    if (!this.socket) {
      this.socket = new JarvisSocket(url, {
        onMessage: (message) => this.handleMessage(message),
        onAudio: ({ pcm, sampleRate }) => this.player?.enqueue(pcm, sampleRate),
        onConnectionState: (connection) => this.patch({ connection }),
      });
      this.socket.connect();
    }
  }

  async startMic(): Promise<void> {
    if (!this.context || !this.socket) throw new Error('Erst verbinden');
    if (!this.capture) {
      this.capture = new MicCapture(this.context, {
        sampleRate: this.sampleRate,
        frameMs: this.frameMs,
        onFrame: (frame) => this.handleFrame(frame),
      });
    }
    await this.capture.start();
    this.gate = new SpeechGate(this.bargeInMinMs);
    this.socket.send({ type: 'mic.toggle', open: true });
    this.patch({ micOpen: true });
    this.note('Mikrofon geöffnet');
  }

  stopMic(): void {
    this.capture?.stop();
    this.capture = null;
    this.gate?.reset();
    this.detector?.reset();
    this.socket?.send({ type: 'mic.toggle', open: false });
    this.patch({ micOpen: false });
    this.note('Mikrofon geschlossen');
  }

  sendText(text: string): void {
    this.socket?.send({ type: 'user.text', text });
    this.patch({ transcript: text, reply: '' });
  }

  /** Manueller Stopp — der Kill-Switch aus Architektur §12. */
  interrupt(reason = 'user_stop'): void {
    this.player?.flush();
    this.socket?.send({
      type: 'user.interrupt',
      played_ms: this.player?.playedMilliseconds ?? null,
      reason,
    });
    this.patch({ state: 'listening' });
  }

  disconnect(): void {
    this.stopMic();
    this.socket?.close();
    this.socket = null;
    this.player?.stop();
    this.player = null;
    this.patch({ connection: 'closed', state: 'idle' });
  }

  get snapshot(): JarvisView {
    return this.view;
  }

  // --- Audio-Eingang -------------------------------------------------------

  private handleFrame(frame: ArrayBuffer): void {
    // Kopie für die VAD anlegen, bevor der Puffer weggeschickt wird.
    const pcm = new Int16Array(frame);
    const floats = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i += 1) floats[i] = pcm[i] / 0x8000;

    this.socket?.sendAudio(frame);
    void this.detectSpeech(floats);
  }

  private async detectSpeech(samples: Float32Array): Promise<void> {
    if (!this.detector || !this.gate) return;
    const probability = await this.detector.process(samples);
    const fired = this.gate.update(probability, this.frameMs);
    // Nur während der Sprachausgabe ist Reinsprechen eine Unterbrechung.
    if (fired && this.view.state === 'speaking') {
      this.note('Barge-in erkannt');
      this.interrupt('barge_in');
    }
  }

  // --- Nachrichten vom Host ------------------------------------------------

  private handleMessage(message: ServerMessage): void {
    switch (message.type) {
      case 'session.ready':
        this.frameMs = message.input_frame_ms;
        this.sampleRate = message.input_sample_rate;
        this.bargeInMinMs = message.barge_in_min_speech_ms;
        this.gate = new SpeechGate(this.bargeInMinMs);
        this.note(`Verbunden — Session ${message.session_id}`);
        break;

      case 'state.changed':
        if (message.state === 'thinking') {
          // Neuer Turn: Zähler der abgespielten Dauer zurücksetzen, sonst
          // wäre `played_ms` beim nächsten Barge-in kumuliert und falsch.
          this.player?.reset();
          this.patch({ reply: '' });
        }
        this.patch({ state: message.state });
        break;

      case 'transcript.partial':
        this.patch({ partial: message.text });
        break;

      case 'transcript.final':
        this.patch({ transcript: message.text, partial: '' });
        break;

      case 'text.delta':
        this.patch({ reply: this.view.reply + message.text });
        break;

      case 'reply.completed':
        this.note(message.interrupted ? 'Turn unterbrochen' : 'Turn fertig');
        break;

      case 'metrics.latency':
        if (message.name === 'speech_end_to_first_audio') {
          this.patch({ lastLatencyMs: Math.round(message.ms) });
          this.note(`Latenz Sprachende → erster Ton: ${Math.round(message.ms)} ms`);
        }
        break;

      case 'error':
        this.patch({ error: message.message });
        this.note(`Fehler: ${message.message}`);
        break;
    }
  }
}

export const initialView = INITIAL;
