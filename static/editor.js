import {RasterHistory, floodFill} from './editor-core.js';

export function paintRaster(canvas, pixels, side) {
  canvas.width = side; canvas.height = side;
  const ctx = canvas.getContext('2d'), frame = ctx.createImageData(side, side);
  for (let i = 0; i < pixels.length; i++) {
    frame.data[4*i] = frame.data[4*i+1] = frame.data[4*i+2] = pixels[i]; frame.data[4*i+3] = 255;
  }
  ctx.putImageData(frame, 0, 0);
}

export class Editor {
  constructor(canvas, onChange, onBusy) {
    this.canvas = canvas; this.history = new RasterHistory(); this.onChange = onChange; this.onBusy = onBusy;
    this.settings = {tool: 'brush', shade: 255, thickness: 16, mode: 'filled', tolerance: 0};
    this.gesture = null; this.render();
    canvas.addEventListener('pointerdown', e => this.start(e));
    canvas.addEventListener('pointermove', e => this.move(e));
    canvas.addEventListener('pointerup', e => this.finish(e));
    canvas.addEventListener('pointercancel', e => { if (e.pointerId === this.gesture?.id) this.cancel(); });
    canvas.addEventListener('lostpointercapture', () => this.cancel());
    window.addEventListener('keydown', e => { if (e.key === 'Escape') this.cancel(); });
    window.addEventListener('blur', () => this.cancel());
  }
  point(e) {
    const rect = this.canvas.getBoundingClientRect();
    return {x: (e.clientX - rect.left) * 512 / rect.width, y: (e.clientY - rect.top) * 512 / rect.height};
  }
  render() { paintRaster(this.canvas, this.history.pixels, 512); }
  start(e) {
    if (this.gesture || e.button !== 0 || !e.isPrimary) return;
    e.preventDefault(); this.canvas.focus();
    this.gesture = {id: e.pointerId, points: [this.point(e)], ...this.settings};
    this.canvas.setPointerCapture(e.pointerId); this.onBusy(true); this.preview();
  }
  move(e) {
    if (this.gesture?.id !== e.pointerId) return;
    this.gesture.points.push(this.point(e)); this.preview();
  }
  preview() {
    this.render();
    const g = this.gesture;
    if (!g || g.tool === 'bucket') return;
    const ctx = this.canvas.getContext('2d'), first = g.points[0], last = g.points.at(-1);
    ctx.fillStyle = ctx.strokeStyle = `rgb(${g.tool === 'eraser' ? 0 : g.shade} ${g.tool === 'eraser' ? 0 : g.shade} ${g.tool === 'eraser' ? 0 : g.shade})`;
    ctx.lineWidth = g.thickness; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.beginPath();
    if (g.tool === 'brush' || g.tool === 'eraser') {
      if (g.points.every(p => p.x === first.x && p.y === first.y)) {
        ctx.arc(first.x, first.y, g.thickness/2, 0, Math.PI*2); ctx.fill();
      } else {
        ctx.moveTo(first.x, first.y); g.points.slice(1).forEach(p => ctx.lineTo(p.x, p.y)); ctx.stroke();
      }
    } else if (g.tool === 'circle') {
        const radius = Math.hypot(last.x - first.x, last.y - first.y);
        if (!radius) return;
        ctx.arc(first.x, first.y, radius, 0, Math.PI*2);
      if (g.mode === 'filled') ctx.fill(); else ctx.stroke();
    }
  }
  finish(e) {
    if (this.gesture?.id !== e.pointerId) return;
    this.gesture.points.push(this.point(e)); this.preview();
    const g = this.gesture;
    let next;
    if (g.tool === 'bucket') {
      const p = g.points[0];
      next = floodFill(this.history.pixels, 512, Math.floor(p.x), Math.floor(p.y), g.shade, g.tolerance);
    } else {
      const rgba = this.canvas.getContext('2d').getImageData(0, 0, 512, 512).data;
      next = new Uint8Array(512*512);
      for (let i=0;i<next.length;i++) next[i] = rgba[4*i];
    }
    this.gesture = null;
    if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
    const changed = this.history.commit(next); this.render(); this.onBusy(false);
    if (changed) this.onChange();
  }
  cancel() {
    if (!this.gesture) return;
    const id = this.gesture.id; this.gesture = null;
    if (this.canvas.hasPointerCapture(id)) this.canvas.releasePointerCapture(id);
    this.render(); this.onBusy(false);
  }
  action(name) {
    if (this.gesture) return;
    const changed = name === 'clear' ? this.history.commit(new Uint8Array(512*512)) : this.history[name]();
    if (changed) { this.render(); this.onChange(); }
  }
}
