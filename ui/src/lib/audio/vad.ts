/**
 * Sprachaktivitätserkennung im Client.
 *
 * Die Erkennung läuft bewusst hier und nicht auf dem Host. Ein Roundtrip
 * iPad → Host → iPad kostet 40–120 ms, das Flush-Ziel liegt unter 60 ms.
 * Also erkennt der Browser, flusht sofort lokal und meldet dem Host erst
 * danach `user.interrupt` (Architektur §5).
 *
 * Silero-VAD wird verwendet, wenn das ONNX-Modell unter
 * `/models/silero_vad.onnx` liegt. Fehlt es, greift eine energiebasierte
 * Erkennung. Die ist schlechter — sie hält lautes Rascheln für Sprache —
 * aber sie hält die Sprachschleife lauffähig, statt sie stumm zu brechen.
 */

export interface VoiceDetector {
  /** Sprachwahrscheinlichkeit 0..1 für einen 16-kHz-Block. */
  process(samples: Float32Array): Promise<number>;
  reset(): void;
  readonly kind: 'silero' | 'energy';
}

const SILERO_WINDOW = 512; // Silero v5 erwartet genau 512 Samples bei 16 kHz
const MODEL_URL = '/models/silero_vad.onnx';

/** Energiebasierter Notbetrieb — kein Modell nötig. */
export class EnergyVad implements VoiceDetector {
  readonly kind = 'energy' as const;
  private noiseFloor = 0.005;

  async process(samples: Float32Array): Promise<number> {
    let sum = 0;
    for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / Math.max(1, samples.length));

    // Grundrauschen langsam nachführen, damit sich der Schwellwert an den
    // Raum anpasst statt fest verdrahtet zu sein.
    if (rms < this.noiseFloor * 1.5) {
      this.noiseFloor = this.noiseFloor * 0.995 + rms * 0.005;
    }
    const ratio = rms / Math.max(this.noiseFloor, 1e-5);
    return Math.max(0, Math.min(1, (ratio - 3) / 7));
  }

  reset(): void {
    this.noiseFloor = 0.005;
  }
}

class SileroVad implements VoiceDetector {
  readonly kind = 'silero' as const;
  private buffer: Float32Array = new Float32Array(0);
  private state: unknown;
  private lastProbability = 0;

  constructor(
    private readonly session: any,
    private readonly ort: any,
  ) {
    this.state = this.zeroState();
  }

  private zeroState(): unknown {
    return new this.ort.Tensor('float32', new Float32Array(2 * 1 * 128), [2, 1, 128]);
  }

  async process(samples: Float32Array): Promise<number> {
    // Eingangsblöcke sind 20 ms (320 Samples), Silero will 512. Puffern.
    const merged = new Float32Array(this.buffer.length + samples.length);
    merged.set(this.buffer);
    merged.set(samples, this.buffer.length);
    this.buffer = merged;

    while (this.buffer.length >= SILERO_WINDOW) {
      const window = this.buffer.subarray(0, SILERO_WINDOW);
      const result = await this.session.run({
        input: new this.ort.Tensor('float32', window, [1, SILERO_WINDOW]),
        state: this.state,
        sr: new this.ort.Tensor('int64', BigInt64Array.from([16000n]), []),
      });
      this.lastProbability = result.output.data[0] as number;
      this.state = result.stateN ?? this.state;
      this.buffer = this.buffer.slice(SILERO_WINDOW);
    }
    return this.lastProbability;
  }

  reset(): void {
    this.buffer = new Float32Array(0);
    this.state = this.zeroState();
    this.lastProbability = 0;
  }
}

export async function createVoiceDetector(): Promise<VoiceDetector> {
  try {
    const head = await fetch(MODEL_URL, { method: 'HEAD' });
    if (!head.ok) throw new Error(`Modell nicht gefunden (${head.status})`);
    const ort = await import('onnxruntime-web');
    const session = await ort.InferenceSession.create(MODEL_URL, {
      executionProviders: ['wasm'],
    });
    return new SileroVad(session, ort);
  } catch (error) {
    console.warn(
      `Silero-VAD nicht verfügbar (${String(error)}) — energiebasierte Erkennung aktiv. ` +
        'Modell nach ui/public/models/silero_vad.onnx legen, siehe docs/phase2-abnahme.md.',
    );
    return new EnergyVad();
  }
}

/**
 * Entscheidet, wann Reinsprechen als Unterbrechung gilt.
 *
 * Die Schwelle von 200 ms zusammenhängender Sprache ist der Unterschied
 * zwischen "reagiert" und "bricht ständig grundlos ab": ein "mhm" oder ein
 * Husten ist ein Backchannel und darf JARVIS nicht stoppen. Eine falsche
 * Unterbrechung fühlt sich schlimmer an als 200 ms Verzögerung.
 */
export class SpeechGate {
  private speechMs = 0;
  private fired = false;

  constructor(
    private readonly minSpeechMs: number,
    private readonly threshold = 0.6,
  ) {}

  /** true genau einmal pro zusammenhängendem Sprachereignis. */
  update(probability: number, frameMs: number): boolean {
    if (probability >= this.threshold) {
      this.speechMs += frameMs;
    } else {
      this.speechMs = 0;
      this.fired = false;
      return false;
    }
    if (!this.fired && this.speechMs >= this.minSpeechMs) {
      this.fired = true;
      return true;
    }
    return false;
  }

  reset(): void {
    this.speechMs = 0;
    this.fired = false;
  }
}
