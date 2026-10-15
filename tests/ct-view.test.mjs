import test from 'node:test';
import assert from 'node:assert/strict';
import {sharedRange,sinogramFrame} from '../static/ct-view.js';
test('sinogram display uses angle horizontally and increasing offset upward',()=>{
  const bytes=sinogramFrame([0,1,2,3,4,5],2,3,0,5);
  const gray=Array.from({length:6},(_,i)=>bytes[4*i]);
  assert.deepEqual(gray,[102,255,51,204,0,153]);
});
test('both sinograms share a finite scale, preserving negative values',()=>{
  assert.deepEqual(sharedRange([0,1],[-2,3]),[-2,3]);
  assert.deepEqual(sharedRange([0,0],[0,0]),[0,1]);
});
