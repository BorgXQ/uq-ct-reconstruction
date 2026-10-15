import assert from 'node:assert/strict';
import test from 'node:test';
import {limits,mapBytes} from '../static/exact-view.js';
test('constant and signed maps have finite quantitative display without changing values',()=>{
  const values=[0,0,0],original=values.slice();assert.deepEqual(limits(values),[0,0]);
  assert.deepEqual(Array.from(mapBytes(values,[0,0])),[128,128,128,255,128,128,128,255,128,128,128,255]);
  assert.deepEqual(values,original);
  const bytes=mapBytes([-1,0,1],[-1,1],{signed:true});assert.equal(bytes[0],0);assert.equal(bytes[2],255);assert.equal(bytes[8],255);assert.equal(bytes[10],0);
});
test('raw endpoints share a scale and fixed image display saturates without mutation',()=>{
  const values=[-.2,.5,1.3];const bytes=mapBytes(values,[0,1]);
  assert.equal(bytes[0],0);assert.equal(bytes[4],128);assert.equal(bytes[8],255);
  assert.deepEqual(values,[-.2,.5,1.3]);assert.deepEqual(limits([-2,0,1,3]),[-2,3]);
});
