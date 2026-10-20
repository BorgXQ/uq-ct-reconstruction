import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ellipseCoverage} from '../static/validation-presentation.js';
test('ellipse uses pixel centers and excludes the exterior from the mean',()=>{
  const values=Array(16).fill(0);for(const i of [5,6,9,10])values[i]=.95;
  const r=ellipseCoverage(values,4,.5,.5);
  assert.equal(r.count,4);assert.equal(r.mean,.95);
  assert.equal(ellipseCoverage(null,4,.5,.5).mean,null);
  assert.equal(ellipseCoverage(values,4,.01,.01).count,0);
  assert.throws(()=>ellipseCoverage(values,4,0,.5));
});
