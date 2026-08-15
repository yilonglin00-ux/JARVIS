/**
 * Wiedergabe der TTS-Chunks.
 *
 * Die eigentliche Arbeit macht `worklets/player-processor.js`. Hier steht
 * nur die Ansteuerung — und der Zähler, wie viel tatsächlich erklungen ist.
 * Dieser Wert geht bei einer Unterbrechung als `played_ms` an den Host und
 * entscheidet dort darüber, was im Gesprächsverlauf landet.
 */

export interface PlayerOptions {
  /** Abtastrate der eingehenden PCM-Daten (kommt vom Host je Frame mit). */
  inputRate?: number;
  onProgress?: (playedMs: number) => void;
}

export class AudioPlayer {
  private node: AudioWorkletNode | null = null;
  private analyser: AnalyserNode | null = null;
  private inputRate: number;
  private playedMs = 0;

  constructor(
    private readonly context: AudioContext,
    private readonly options: PlayerOptions = {},
  ) {
    this.inputRate = options.inputRate ?? 24000;
  }

  async start(): Promise<void> {
    if (this.node) return;
    await this.context.audioWorklet.addModule('/worklets/player-processor.js');
    this.node = new AudioWorkletNode(this.context, 'player-processor', {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { inputRate: this.inputRate },
    });
    this.node.port.onmessage = (event) => {
      const data = event.data as { type: string; playedMs: number };
      if (data.type === 'progress') {
        this.playedMs = data.playedMs;
        this.options.onProgress?.(data.playedMs);
      }
    };

    // Der Analyser hängt am tatsächlich abgespielten Signal — Grundlage für
    // Wellenform und visuelle Haptik in Phase 7.
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 512;
    this.node.connect(this.analyser);
    this.analyser.connect(this.context.destination);
  }

  get analyserNode(): AnalyserNode | null {
    return this.analyser;
  }

  /** Tatsächlich am Lautsprecher gewesene Audiodauer des laufenden Turns. */
  get playedMilliseconds(): number {
    return this.playedMs;
  }

  enqueue(pcm: Int16Array, sampleRate: number): void {
    if (!this.node) return;
    if (sampleRate !== this.inputRate) {
      this.inputRate = sampleRate;
      this.node.port.postMessage({ type: 'inputRate', value: sampleRate });
    }
    const floats = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i += 1) {
      floats[i] = pcm[i] / 0x8000;
    }
    this.node.port.postMessage(floats.buffer, [floats.buffer]);
  }

  /**
   * Barge-in: Puffer sofort leeren.
   *
   * Muss innerhalb eines Render-Quantums greifen (unter 3 ms). Genau
   * deshalb liegt der Puffer im Worklet und nicht in einem <audio>-Element.
   */
  flush(): void {
    this.node?.port.postMessage({ type: 'flush' });
  }

  /** Vor jedem neuen Turn: Zähler zurücksetzen. */
  reset(): void {
    this.playedMs = 0;
    this.node?.port.postMessage({ type: 'reset' });
  }

  stop(): void {
    this.node?.disconnect();
    this.analyser?.disconnect();
    this.node = null;
    this.analyser = null;
  }
}
