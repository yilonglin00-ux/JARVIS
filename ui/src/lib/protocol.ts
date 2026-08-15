/**
 * Gegenstück zu `src/jarvis/interfaces/protocol.py`.
 *
 * Beide Dateien werden von Hand gepflegt; `tests/test_protocol.py` prüft,
 * dass keine Seite einen Nachrichtentyp kennt, den die andere nicht kennt.
 * Ohne diese Prüfung fällt eine Abweichung erst auf dem iPad auf — als
 * Verbindung, die nichts tut.
 */

export const PROTOCOL_VERSION = 1;

// --- Host -> Client -----------------------------------------------------------

export type ConversationState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface SessionReady {
  type: 'session.ready';
  session_id: string;
  protocol_version: number;
  language: string;
  input_sample_rate: number;
  input_frame_ms: number;
  barge_in_min_speech_ms: number;
}

export interface StateChangedMsg {
  type: 'state.changed';
  state: ConversationState;
  previous: ConversationState;
}

export interface TranscriptMsg {
  type: 'transcript.partial' | 'transcript.final';
  text: string;
  confidence: number | null;
}

export interface TextDeltaMsg {
  type: 'text.delta';
  text: string;
}

export interface ReplyCompletedMsg {
  type: 'reply.completed';
  spoken: string;
  interrupted: boolean;
}

export interface LatencyMsg {
  type: 'metrics.latency';
  name: string;
  ms: number;
}

export interface ErrorMsg {
  type: 'error';
  message: string;
  recoverable: boolean;
}

export type ServerMessage =
  | SessionReady
  | StateChangedMsg
  | TranscriptMsg
  | TextDeltaMsg
  | ReplyCompletedMsg
  | LatencyMsg
  | ErrorMsg;

// --- Client -> Host -----------------------------------------------------------

export interface UserTextMsg {
  type: 'user.text';
  text: string;
}

/**
 * Barge-in. Der Client hat lokal bereits geflusht, bevor er das hier sendet.
 *
 * `played_ms` ist die tatsächlich abgespielte Audiodauer dieses Turns. Nur
 * der Client kennt sie — der Host weiß bloß, was er gesendet hat. Fehlt der
 * Wert, geht der Host konservativ davon aus, dass nichts gehört wurde.
 */
export interface UserInterruptMsg {
  type: 'user.interrupt';
  played_ms: number | null;
  reason: string;
}

export interface MicToggleMsg {
  type: 'mic.toggle';
  open: boolean;
}

export interface PingMsg {
  type: 'ping';
}

export type ClientMessage = UserTextMsg | UserInterruptMsg | MicToggleMsg | PingMsg;

// --- Binärrahmen --------------------------------------------------------------
//
// Aufwärts: rohes PCM ohne Kopf (16 kHz, 16 bit, mono, Little Endian).
// Abwärts: 12-Byte-Kopf, damit der Client nach einem Barge-in veraltete
// Chunks an der Sequenznummer erkennen kann.

export const AUDIO_HEADER_SIZE = 12;
const MAGIC_J = 0x4a; // 'J'
const MAGIC_A = 0x41; // 'A'

export interface DecodedAudioFrame {
  seq: number;
  sampleRate: number;
  pcm: Int16Array;
}

export function decodeAudioFrame(buffer: ArrayBuffer): DecodedAudioFrame {
  if (buffer.byteLength < AUDIO_HEADER_SIZE) {
    throw new Error('Audioframe zu kurz');
  }
  const view = new DataView(buffer);
  if (view.getUint8(0) !== MAGIC_J || view.getUint8(1) !== MAGIC_A) {
    throw new Error('Unbekannte Audiokennung');
  }
  const version = view.getUint8(2);
  if (version !== PROTOCOL_VERSION) {
    throw new Error(`Protokollversion ${version} wird nicht unterstützt`);
  }
  return {
    seq: view.getUint32(4, true),
    sampleRate: view.getUint32(8, true),
    // slice() statt subarray(): der Puffer wird gleich an den Worklet
    // übertragen und wäre danach hier nicht mehr lesbar.
    pcm: new Int16Array(buffer.slice(AUDIO_HEADER_SIZE)),
  };
}
