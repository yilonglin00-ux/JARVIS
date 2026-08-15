/**
 * Mikrofon-Aufnahme im Audio-Thread.
 *
 * Läuft bewusst als AudioWorklet und nicht im UI-Thread: sobald das HUD in
 * Phase 7 mit 60 fps animiert, würde eine Aufnahme im Haupt-Thread stottern
 * und Frames verlieren.
 *
 * Aufgabe: Float32 bei Kontextrate (auf dem iPad meist 48 kHz) entgegen-
 * nehmen, auf 16 kHz bringen, in 20-ms-Blöcke schneiden und als Int16-PCM
 * an den Haupt-Thread schicken.
 */

const TARGET_RATE = 16000;
const FRAME_MS = 20;

class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || TARGET_RATE;
    this.frameSamples = Math.round((this.targetRate * (opts.frameMs || FRAME_MS)) / 1000);
    this.ratio = sampleRate / this.targetRate;
    this.buffer = new Int16Array(this.frameSamples);
    this.filled = 0;
    // Bruchteil-Position im Eingangssignal, über Blockgrenzen hinweg.
    this.cursor = 0;
    this.muted = false;

    this.port.onmessage = (event) => {
      if (event.data && event.data.type === 'mute') {
        this.muted = Boolean(event.data.value);
      }
    };
  }

  /**
   * Downsampling mit gleitendem Mittel über das Quellintervall.
   *
   * Reine Dezimation (jedes dritte Sample nehmen) erzeugt Aliasing und
   * damit hörbar schlechtere Transkripte. Das Mittel über das Intervall
   * ist ein grober, aber wirksamer Tiefpass und kostet fast nichts.
   */
  resampleInto(input) {
    const out = [];
    let position = this.cursor;
    while (position + this.ratio <= input.length) {
      const start = Math.floor(position);
      const end = Math.min(input.length, Math.floor(position + this.ratio));
      let sum = 0;
      let count = 0;
      for (let i = start; i < end; i += 1) {
        sum += input[i];
        count += 1;
      }
      out.push(count > 0 ? sum / count : 0);
      position += this.ratio;
    }
    this.cursor = position - input.length;
    if (this.cursor < 0) this.cursor = 0;
    return out;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    const samples = this.resampleInto(channel);
    for (let i = 0; i < samples.length; i += 1) {
      const clamped = Math.max(-1, Math.min(1, this.muted ? 0 : samples[i]));
      this.buffer[this.filled] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      this.filled += 1;
      if (this.filled === this.frameSamples) {
        const frame = this.buffer.slice();
        this.port.postMessage(frame.buffer, [frame.buffer]);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor('capture-processor', CaptureProcessor);
