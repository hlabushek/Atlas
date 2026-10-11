/** Энергетический VAD: PCM 16 кГц, кадры 20 мс, предзапись и хвост речи. */
export class VoiceVad {
  private pending: Float32Array[] = [];
  private voiced = 0;
  private quiet = 0;
  private active = false;
  constructor(public threshold = 0.012) {}

  feed(frame: Float32Array): {frames: Float32Array[]; start: boolean; end: boolean; rms: number} {
    const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / Math.max(1, frame.length));
    const speech = rms >= this.threshold;
    if (!this.active) {
      this.pending.push(frame);
      if (this.pending.length > 10) this.pending.shift();
      this.voiced = speech ? this.voiced + 1 : 0;
      if (this.voiced < 4) return {frames: [], start: false, end: false, rms};
      this.active = true;
      this.quiet = 0;
      const frames = this.pending;
      this.pending = [];
      return {frames, start: true, end: false, rms};
    }
    this.quiet = speech ? 0 : this.quiet + 1;
    const end = this.quiet >= 20;
    if (end) this.reset();
    return {frames: [frame], start: false, end, rms};
  }

  reset() {this.pending = []; this.voiced = 0; this.quiet = 0; this.active = false;}
}

export function pcm16(frame: Float32Array): ArrayBuffer {
  const data = new ArrayBuffer(frame.length * 2);
  const view = new DataView(data);
  frame.forEach((sample, index) => view.setInt16(index * 2,
    Math.round(Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767)), true));
  return data;
}
