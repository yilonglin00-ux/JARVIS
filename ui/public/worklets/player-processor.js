/**
 * Wiedergabe mit sofort leerbarem Puffer.
 *
 * Das ist der Grund, warum hier kein <audio>-Element steht: nach `pause()`
 * spielt ein <audio>-Element seinen bereits gepufferten Inhalt zu Ende.
 * Barge-in wäre damit unmöglich — man würde JARVIS noch eine Sekunde
 * weiterreden hören, nachdem man ihn unterbrochen hat.
 *
 * Hier liegt der Puffer in diesem Prozessor und ist in einem Render-Quantum
 * (unter 3 ms) restlos leer.
 *
 * Nebenaufgabe: mitzählen, wie viel Audio *tatsächlich* am Lautsprecher war.
 * Nur der Client weiß das. Der Host erfährt es über `played_ms` und kann
 * daraus bestimmen, was der Nutzer gehört hat.
 */

class PlayerProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.inputRate = opts.inputRate || 24000;
    this.ratio = this.inputRate / sampleRate; // Eingangs- pro Ausgangssample
    this.queue = [];
    this.current = null;
    this.cursor = 0;
    this.playedSamples = 0;
    this.reportEvery = Math.round(sampleRate / 20); // ~alle 50 ms melden
    this.sinceReport = 0;
    this.wasPlaying = false;

    this.port.onmessage = (event) => {
      const data = event.data;
      if (data instanceof ArrayBuffer) {
        this.queue.push(new Float32Array(data));
        return;
      }
      if (!data || !data.type) return;
      if (data.type === 'flush') {
        // Barge-in: alles verwerfen, was noch nicht gehört wurde.
        this.queue.length = 0;
        this.current = null;
        this.cursor = 0;
        this.report(true);
      } else if (data.type === 'reset') {
        this.queue.length = 0;
        this.current = null;
        this.cursor = 0;
        this.playedSamples = 0;
        this.sinceReport = 0;
      } else if (data.type === 'inputRate') {
        this.inputRate = data.value;
        this.ratio = this.inputRate / sampleRate;
      }
    };
  }

  report(force) {
    this.port.postMessage({
      type: 'progress',
      playedMs: (this.playedSamples / sampleRate) * 1000,
      queued: this.queue.length,
      final: Boolean(force),
    });
    this.sinceReport = 0;
  }

  nextSample() {
    while (this.current === null || this.cursor >= this.current.length) {
      if (this.queue.length === 0) return null;
      this.current = this.queue.shift();
      this.cursor = 0;
    }
    // Lineare Interpolation von Eingangs- auf Kontextrate (meist 24 -> 48 kHz).
    const index = Math.floor(this.cursor);
    const frac = this.cursor - index;
    const a = this.current[index];
    const b = index + 1 < this.current.length ? this.current[index + 1] : a;
    this.cursor += this.ratio;
    return a + (b - a) * frac;
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    const left = output[0];
    let playing = false;

    for (let i = 0; i < left.length; i += 1) {
      const sample = this.nextSample();
      if (sample === null) {
        left[i] = 0;
      } else {
        left[i] = sample;
        this.playedSamples += 1;
        playing = true;
      }
    }
    for (let c = 1; c < output.length; c += 1) {
      output[c].set(left);
    }

    this.sinceReport += left.length;
    if (playing && this.sinceReport >= this.reportEvery) {
      this.report(false);
    }
    if (this.wasPlaying && !playing && this.queue.length === 0) {
      this.report(true);
    }
    this.wasPlaying = playing;
    return true;
  }
}

registerProcessor('player-processor', PlayerProcessor);
