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
test('row plot centers the gray band on the single-trial red reconstruction',async()=>{
  const {rowPlot}=await import('../static/validation-presentation.js');
  const previous=globalThis.document;
  globalThis.document={createElementNS:()=>({attrs:{},setAttribute(k,v){this.attrs[k]=v;}})};
  const svg={children:[],replaceChildren(){this.children=[];},append(el){this.children.push(el);}};
  try{
    rowPlot(svg,[0,0,1,1],{center:[0,0,.2,.8],lower:[0,0,-.1,.5],upper:[0,0,.5,1.1]},2,1);
    const lines=svg.children.filter(el=>el.attrs['data-role']==='trial-center');
    assert.equal(lines.length,1);assert.equal(lines[0].attrs.stroke,'#c52b32');assert.equal(lines[0].attrs['stroke-dasharray'],'');
    assert.equal(svg.children.some(el=>el.attrs['data-role']==='mean'),false);
    const points=s=>s.split(' ').map(p=>p.split(',').map(Number));
    const center=points(lines[0].attrs.points),band=points(svg.children.find(el=>el.attrs['data-role']==='interval-band').attrs.points);
    for(let i=0;i<2;i++)assert.ok(Math.abs(center[i][1]-(band[i][1]+band[3-i][1])/2)<1e-10);
  }finally{globalThis.document=previous;}
});
