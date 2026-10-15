import test from 'node:test';
import assert from 'node:assert/strict';
import {floodFill, RasterHistory, squareBounds, base64} from '../static/editor-core.js';

test('four-connected fill leaves disconnected and diagonal regions untouched', () => {
  const source=Uint8Array.from([0,9,0,9,0,9,0,9,0]);
  assert.deepEqual([...floodFill(source,3,0,0,4,0)],[4,9,0,9,0,9,0,9,0]);
  assert.equal(source[0],0);
});
test('seed tolerance never drifts along a ramp, including replacement equal to seed', () => {
  const source=Uint8Array.from([0,4,8,12,99,99,99,99,99,99,99,99,99,99,99,99]);
  assert.deepEqual([...floodFill(source,4,0,0,0,5)].slice(0,4),[0,0,8,12]);
  assert.deepEqual([...floodFill(source,4,0,0,30,5)].slice(0,4),[30,30,8,12]);
  assert.deepEqual(floodFill(source,4,0,0,0,0),source);
});
test('closed outline contains fill; opening lets it escape', () => {
  const source=new Uint8Array(49);
  for(let i=1;i<6;i++) {source[7+i]=9;source[35+i]=9;source[i*7+1]=9;source[i*7+5]=9;}
  assert.equal(floodFill(source,7,3,3,20,0)[0],0);
  source[1*7+3]=0;
  assert.equal(floodFill(source,7,3,3,20,0)[0],20);
});
test('full-size fill terminates and no-op history stays empty', () => {
  const history=new RasterHistory();
  assert.equal(history.commit(floodFill(history.pixels,512,1,1,0,0)),false);
  assert.equal(history.revision,0);
  const filled=floodFill(history.pixels,512,1,1,137,0);
  assert.ok(filled.every(x=>x===137));assert.equal(history.commit(filled),true);
  assert.equal(history.undoStack.length,1);
  history.undo();assert.ok(history.pixels.every(x=>x===0));history.redo();assert.deepEqual(history.pixels,filled);
});
test('history bounds, clear, redo branching, defensive commit', () => {
  const h=new RasterHistory(2,50);
  for(let i=1;i<=60;i++) h.commit(new Uint8Array(4).fill(i));
  assert.equal(h.undoStack.length,50);
  for(let i=0;i<60;i++)h.undo();assert.equal(h.pixels[0],10);
  for(let i=0;i<60;i++)h.redo();assert.equal(h.pixels[0],60);
  h.commit(new Uint8Array(4));h.undo();assert.equal(h.pixels[0],60);h.redo();assert.equal(h.pixels[0],0);
  h.undo();const a=new Uint8Array(4).fill(80);h.commit(a);a[0]=9;assert.equal(h.pixels[0],80);assert.equal(h.redo(),false);
});
test('square geometry in every quadrant and zero displacement convention', () => {
  for(const [x,y] of [[4,2],[-4,2],[-2,-4],[2,-4],[0,4]]) {
    const b=squareBounds({x:10,y:10},{x:10+x,y:10+y});assert.equal(b.side,4);
    assert.equal(b.x,x<0?6:10);assert.equal(b.y,y<0?6:10);
  }
});
test('byte serialization preserves the entire raster', () => {
  const bytes=Uint8Array.from({length:512*512},(_,i)=>i%256);
  assert.deepEqual(new Uint8Array(Buffer.from(base64(bytes),'base64')),bytes);
});
