import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';

test('optional calibration, manual precedence, cached fallback and stale jobs',async()=>{
  const fields=new Map();
  function field(id,value=''){
    const listeners={};
    const el={value,checked:false,validity:{badInput:false},textContent:'',addEventListener:(name,fn)=>{listeners[name]=fn;},fire:name=>listeners[name]?.()};
    fields.set(id,el);return el;
  }
  const obs=field('rq-observation'),nu=field('rq-nu','0.5'),manual=field('rq-M'),cal=field('rq-calibrated-M'),enabled=field('calibrate-source'),status=field('rq-source-status'),retry=field('rq-source-retry');
  const pending=[];let requests=0,cancels=0;
  globalThis.document={getElementById:id=>fields.get(id)};
  globalThis.crypto??=webcrypto;
  globalThis.__calibrationApi={
    api:async(url)=>{if(url.endsWith('/cancel')){cancels++;return {};}
      assert.equal(url,'/api/source-calibrations');requests++;return {result:{job_id:`job-${requests}`}};},
    watch:()=>new Promise(resolve=>pending.push(resolve))
  };
  const source=(await readFile(new URL('../static/source-calibration.js',import.meta.url),'utf8')).replace("import {api,watch} from './ct.js';","const {api,watch}=globalThis.__calibrationApi;");
  const mod=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  const result=(M=8.123456)=>({status:'completed',result:{status:'compatible',M,nu:Number(nu.value),snapshot_id:'drawing',reason:'Numerical check passed.'}});
  obs.value='one';mod.observationChanged(()=>{});await tick();
  assert.equal(requests,0);assert.equal(mod.sourceReady(),false);
  manual.value='6';manual.fire('input');assert.equal(mod.sourceReady(),true);
  assert.equal(mod.calibratedSource().M,6);assert.match(mod.calibratedSource().provenance,/Manually/);
  enabled.checked=true;enabled.fire('change');await tick();assert.equal(requests,1);
  pending.shift()(result());await tick();
  assert.equal(manual.value,'6');assert.equal(cal.value,'8.1235');assert.equal(mod.calibratedSource().M,6);
  manual.value='';manual.fire('input');assert.equal(mod.calibratedSource().M,8.123456);
  manual.value='-1';manual.fire('input');assert.equal(mod.sourceReady(),false);
  manual.value='0';manual.fire('input');assert.equal(mod.calibratedSource().M,0);
  manual.value='';enabled.checked=false;enabled.fire('change');
  nu.value='0.3';nu.fire('input');nu.fire('blur');await tick();
  assert.equal(requests,1);assert.equal(mod.sourceReady(),false);assert.equal(cal.value,'');
  nu.value='0.5';nu.fire('input');assert.equal(mod.calibratedSource().M,8.123456);
  obs.value='two';mod.observationChanged(()=>{});await tick();assert.equal(requests,1);assert.equal(mod.sourceReady(),false);
  manual.value='9';manual.fire('input');assert.equal(mod.sourceReady(),true);
  // An explicit request is still available while automatic calibration is disabled.
  retry.onclick();await tick();assert.equal(requests,2);
  pending.shift()({status:'completed',result:{status:'unresolved',M:null,nu:0.5,reason:'Weak directions.'}});await tick();
  assert.equal(mod.calibratedSource().M,9);assert.match(status.textContent,/Weak directions/);
  manual.value='';manual.fire('input');assert.equal(mod.sourceReady(),false);
  // Cancel a pending result and ensure it cannot replace the next observation's source.
  retry.onclick();await tick();
  obs.value='three';mod.observationChanged(()=>{});pending.shift()(result(99));await tick();
  assert.equal(cal.value,'');assert.equal(mod.sourceReady(),false);assert.ok(cancels>0);
  delete globalThis.__calibrationApi;delete globalThis.document;
});
