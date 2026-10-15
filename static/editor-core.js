// Pure scalar operations shared by the editor and deterministic tests.
export function floodFill(source, side, x, y, shade, tolerance) {
  const result = source.slice();
  if (x < 0 || y < 0 || x >= side || y >= side) return result;
  const start = y * side + x, seed = source[start];
  if (tolerance === 0 && seed === shade) return result;
  const visited = new Uint8Array(source.length), queue = new Int32Array(source.length);
  let head = 0, tail = 1;
  queue[0] = start; visited[start] = 1;
  while (head < tail) {
    const i = queue[head++];
    if (Math.abs(source[i] - seed) > tolerance) continue;
    result[i] = shade;
    const col = i % side;
    for (const j of [col > 0 ? i - 1 : -1, col < side - 1 ? i + 1 : -1, i - side, i + side]) {
      if (j >= 0 && j < source.length && !visited[j]) { visited[j] = 1; queue[tail++] = j; }
    }
  }
  return result;
}

export class RasterHistory {
  constructor(side = 512, limit = 50) {
    this.side = side; this.limit = limit; this.pixels = new Uint8Array(side * side);
    this.undoStack = []; this.redoStack = []; this.revision = 0;
  }
  commit(next) {
    if (next.length !== this.pixels.length) throw new Error('Raster size mismatch');
    if (this.pixels.every((value, i) => value === next[i])) return false;
    this.undoStack.push(this.pixels.slice());
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.pixels = next.slice(); this.redoStack = []; this.revision++;
    return true;
  }
  undo() { return this.move(this.undoStack, this.redoStack); }
  redo() { return this.move(this.redoStack, this.undoStack); }
  move(from, to) {
    if (!from.length) return false;
    to.push(this.pixels); this.pixels = from.pop(); this.revision++; return true;
  }
}

export function squareBounds(start, end) {
  const dx = end.x - start.x, dy = end.y - start.y, side = Math.max(Math.abs(dx), Math.abs(dy));
  return {x: dx < 0 ? start.x - side : start.x, y: dy < 0 ? start.y - side : start.y, side};
}

export function base64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}
