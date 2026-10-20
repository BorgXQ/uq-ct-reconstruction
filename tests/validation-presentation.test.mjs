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
test('Plotly traces preserve the selected reconstruction and raw intervals',async()=>{
  const {rowFigure}=await import('../static/validation-presentation.js');
  const truth=[0,0,1,1],trial={center:[0,0,.2,.8],lower:[0,0,-.1,.5],upper:[0,0,.5,1.1]};
  const figure=rowFigure(truth,trial,2,1),data=figure.data;
  const center=data.find(t=>t.meta.role==='trial-center');
  assert.deepEqual(center.y,[.2,.8]);assert.deepEqual(center.x,[0,1]);
  assert.equal(center.name,'Landweber mean');assert.equal(center.line.color,'#c52b32');
  assert.deepEqual(data.find(t=>t.meta.role==='truth').y,[1,1]);
  const lower=data.find(t=>t.meta.role==='interval-lower'),upper=data.find(t=>t.meta.role==='interval-upper');
  assert.deepEqual(lower.y,[-.1,.5]);assert.deepEqual(upper.y,[.5,1.1]);
  assert.equal(upper.fill,'tonexty');assert.equal(upper.name,'95% Confidence interval');
  assert.deepEqual(center.customdata,[[-.1,.5],[.5,1.1]]);
  assert.ok(figure.layout.yaxis.range[0]<-.1&&figure.layout.yaxis.range[1]>1.1);
  assert.equal(rowFigure(truth,trial,2,1,.01).data[1].name,'99% Confidence interval');
  const missing=rowFigure(truth,{center:trial.center},2,1);
  assert.equal(missing.data.length,2);assert.match(missing.layout.annotations[0].text,/unavailable/);
  assert.deepEqual(trial.lower,[0,0,-.1,.5]);
});
