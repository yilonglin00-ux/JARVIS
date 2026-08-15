/**
 * Mikrofonaufnahme.
 *
 * Die drei Flags in `getUserMedia` sind der wichtigste Teil dieser Datei.
 * `echoCancellation` aktiviert die WebRTC-Echounterdrückung von Safari und
 * rechnet die eigene Sprachausgabe aus dem Mikrofonsignal heraus. Ohne sie
 * würde JARVIS über Lautsprecher sich selbst hören und sich permanent
 * selbst unterbrechen — genau das Problem, für das man auf dem Desktop
 * eigenen AEC-Code bräuchte.
 */

export interface CaptureOptions {
  sampleRate: number;
  frameMs: number;
  onFrame: (pcm: ArrayBuffer) => void;
}

export class MicCapture {
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private muted = false;

  constructor(
    private readonly context: AudioContext,
    private readonly options: CaptureOptions,
  ) {}

  get active(): boolean {
    return this.stream !== null;
  }

  async start(): Promise<void> {
    if (this.stream) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
      video: false,
    });

    await this.context.audioWorklet.addModule('/worklets/capture-processor.js');
    this.node = new AudioWorkletNode(this.context, 'capture-processor', {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      processorOptions: {
        targetRate: this.options.sampleRate,
        frameMs: this.options.frameMs,
      },
    });
    this.node.port.onmessage = (event) => this.options.onFrame(event.data as ArrayBuffer);

    this.source = this.context.createMediaStreamSource(this.stream);
    this.source.connect(this.node);
  }

  /** Stummschalten, ohne das Mikrofon freizugeben. */
  setMuted(muted: boolean): void {
    this.muted = muted;
    this.node?.port.postMessage({ type: 'mute', value: muted });
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !muted;
    });
  }

  get isMuted(): boolean {
    return this.muted;
  }

  /**
   * Mikrofon vollständig freigeben.
   *
   * Nicht nur stumm schalten: erst `stop()` auf den Tracks schließt das
   * Mikrofon wirklich und lässt die Systemanzeige von iPadOS erlöschen.
   * Alles andere wäre eine Vertrauenslüge.
   */
  stop(): void {
    this.source?.disconnect();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source = null;
    this.node = null;
    this.stream = null;
  }
}
