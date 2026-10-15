import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const base=process.env.TEST_URL||'http://127.0.0.1:5012';
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}});
const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
const check=s=>console.log('PASS '+s);
const json=async url=>(await page.request.get(base+url)).json();
const meta=async id=>JSON.parse(await page.locator('#'+id).textContent());
const draft=async()=>{await page.click('#draft-tab');await page.waitForFunction(()=>document.querySelector('#preview-status').textContent.includes('Backend conversion complete'));};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function draw(x,y,xx,yy){await page.locator('#editor').scrollIntoViewIfNeeded();const b=await page.locator('#editor').boundingBox();await page.mouse.move(b.x+x*b.width/512,b.y+y*b.height/512);await page.mouse.down();await page.mouse.move(b.x+xx*b.width/512,b.y+yy*b.height/512,{steps:3});await page.mouse.up();await draft();}
async function field(id,value){await page.fill('#'+id,String(value));await page.locator('#'+id).press('Tab');}
async function terminal(id){for(let i=0;i<1000;i++){const j=await json('/api/jobs/'+id);if(['completed','canceled','failed'].includes(j.status))return j;await sleep(10);}throw Error('Job timeout');}
async function submitScan(){const response=page.waitForResponse(r=>r.url()===base+'/api/acquisitions'&&r.request().method()==='POST');await page.click('#generate-scan');const j=await(await response).json();assert.ok(j.job_id,JSON.stringify(j));const done=await terminal(j.job_id);assert.equal(done.status,'completed',JSON.stringify(done));await page.waitForFunction(id=>document.querySelector('#measure-observation').value===id,done.result.observation_id);return done.result;}
async function run(K){await field('iterations',K);const response=page.waitForResponse(r=>r.url()===base+'/api/reconstructions'&&r.request().method()==='POST');await page.click('#run-reconstruction');const j=await(await response).json();assert.ok(j.job_id,JSON.stringify(j));const done=await terminal(j.job_id);assert.equal(done.status,'completed',JSON.stringify(done));await page.waitForFunction(id=>document.querySelector('#reconstructions').value===id,done.result.reconstruction_id);await page.waitForFunction(k=>document.querySelector('#iteration-status').dataset.iteration===String(k),K);return done.result.reconstruction_id;}
try{
  await page.goto(base);await draft();
  await field('shade',192);await page.click('[data-tool=circle]');await draw(160,160,255,160);
  await field('shade',64);await page.click('[data-tool=square]');await draw(300,300,410,370);
  await field('shade',137);await page.click('[data-tool=brush]');await draw(60,400,180,420);
  await page.click('#freeze');await page.waitForFunction(()=>document.querySelector('#freeze-status').textContent.startsWith('Frozen snapshot'));
  const source=await meta('metadata');const frozenSource=await json('/api/snapshots/'+source.snapshot_id);await page.waitForFunction(()=>!document.querySelector('#nav-measure').disabled);
  await page.click('#nav-measure');assert.equal(await page.locator('#measure-snapshot').inputValue(),source.snapshot_id);
  const clean=await submitScan();let scan=await meta('scan-metadata');assert.equal(scan.observation.noise.sigma,0);
  const acq=await json('/api/acquisitions/'+clean.acquisition_id),observation0=await json('/api/observations/'+clean.observation_id);
  assert.deepEqual(observation0.y,acq.y_clean);assert.equal(acq.shape[0],90);assert.equal(acq.shape[1],47);
  await field('ray-angle',12);await field('ray-detector',18);await page.click('#inspect-ray');
  assert.ok((await page.locator('#ray-info').textContent()).includes(acq.y_clean[12*47+18].toPrecision(17)));
  // Compare a display cell's color against the actual shared-scale transformation.
  const low=Number(await page.locator('#sino-low').textContent()),high=Number(await page.locator('#sino-high').textContent());
  const imageByte=await page.locator('#clean-sinogram').evaluate(c=>c.getContext('2d').getImageData(12,47-1-18,1,1).data[0]);
  assert.ok(Math.abs(imageByte-Math.round(255*(acq.y_clean[12*47+18]-low)/(high-low)))<=1);
  check('M1 drawing → frozen float64 truth → 90×47 clean scan, axes, sample inspection and orientation');
  await field('sigma',.01);await field('noise-seed',19);
  const noiseResponse=page.waitForResponse(r=>r.url().endsWith('/observations')&&r.request().method()==='POST');
  await page.click('#new-observation');const nj=await(await noiseResponse).json();const nd=await terminal(nj.job_id);
  await page.waitForFunction(id=>document.querySelector('#measure-observation').value===id,nd.result.observation_id);
  const noisy=await json('/api/observations/'+nd.result.observation_id);
  assert.equal(noisy.acquisition_id,clean.acquisition_id);assert.ok(noisy.y.some(v=>v<0));assert.notDeepEqual(noisy.y,observation0.y);
  await page.waitForFunction(()=>!document.querySelector('#nav-reconstruct').disabled);await page.screenshot({path:'/tmp/ct-m2-measure.png',fullPage:true});
  await page.click('#nav-reconstruct');const recid=await run(201);const record=await json('/api/reconstructions/'+recid);
  for(const k of [0,1,2,100,201]){
    await field('inspect-iteration',k);await page.waitForFunction(k=>document.querySelector('#iteration-status').dataset.iteration===String(k),k);
    const cp=await json(`/api/reconstructions/${recid}/checkpoints/${k}`);assert.equal(cp.iteration,k);
    if(k===2)assert.equal(cp.origin,'replayed');
    if(k===0)assert.ok(cp.pixels.every(x=>x===0));
  }
  assert.deepEqual((await json('/api/observations/'+noisy.observation_id)).y,noisy.y);
  await field('ct-row',10);await field('ct-column',10);await page.click('#inspect-reconstruction-pixel');
  assert.ok((await page.locator('#reconstruction-pixel').textContent()).includes(record.pixels[330].toPrecision(17)));
  assert.match(await page.locator('#raw-range').textContent(),/Display clips/);
  assert.equal(await page.locator('#residual-chart polyline').count(),1);assert.equal(await page.locator('#error-chart polyline').count(),1);
  await page.screenshot({path:'/tmp/ct-m2-reconstruct.png',fullPage:true});
  check('seeded noise preserved; full Landweber, base/replayed iterates, unclipped raw values and separate metric plots');
  // Delay one checkpoint reply while another is selected.
  let release,entered;const held=new Promise(r=>release=r),arrived=new Promise(r=>entered=r);
  await page.route(`**/api/reconstructions/${recid}/checkpoints/3`,async route=>{const response=await route.fetch();entered();await held;await route.fulfill({response});});
  await field('inspect-iteration',3);await arrived;await field('inspect-iteration',6);
  await page.waitForFunction(()=>document.querySelector('#iteration-status').dataset.iteration==='6');release();await sleep(250);
  assert.equal(await page.locator('#iteration-status').getAttribute('data-iteration'),'6');await page.unroute(`**/api/reconstructions/${recid}/checkpoints/3`);
  check('late checkpoint response cannot relabel a newer inspected iteration');
  await page.click('#nav-measure');await page.selectOption('#measure-observation',clean.observation_id);
  await page.waitForFunction(id=>document.querySelector('#scan-metadata').textContent.includes(id),clean.observation_id);
  assert.equal((await meta('scan-metadata')).observation.noise.sigma,0);
  await page.selectOption('#measure-observation',noisy.observation_id);await page.waitForFunction(id=>document.querySelector('#scan-metadata').textContent.includes(id),noisy.observation_id);
  check('historical observations restore their own captured noise and geometry');
  // Hold the job-submission reply, edit drawing and prospective settings before completion is handled.
  let releaseScan,scanEntered;const scanHeld=new Promise(r=>releaseScan=r),scanArrived=new Promise(r=>scanEntered=r);
  await page.route('**/api/acquisitions',async route=>{if(route.request().method()==='POST'){const response=await route.fetch();scanEntered();await scanHeld;await route.fulfill({response});}else await route.continue();});
  await page.click('#generate-scan');await scanArrived;await field('n-angles',30);
  await page.click('#nav-draw');await page.click('#clear');await draft();await page.selectOption('#resolution','16');await draft();releaseScan();await sleep(500);
  await page.click('#nav-measure');assert.match(await page.locator('#measure-status').textContent(),/Live editor differs/);
  assert.equal((await meta('scan-metadata')).observation.observation_id,noisy.observation_id);await page.unroute('**/api/acquisitions');
  assert.deepEqual((await json('/api/snapshots/'+source.snapshot_id)).pixels,frozenSource.pixels);
  check('continued drawing, resolution and form changes cannot mutate captures or attach late scans to new settings');
  // Large enough build to allow actual queued and running cancellation, without synthetic sleep in production.
  const payload={request_id:'cancel-running',snapshot_id:source.snapshot_id,geometry:{n_angles:180,n_detectors:128,start_degrees:0,end_degrees:180},noise:{sigma:0,seed:0}};
  const running=await(await page.request.post(base+'/api/acquisitions',{data:payload})).json();
  const queued=await(await page.request.post(base+'/api/acquisitions',{data:{...payload,request_id:'cancel-queued'}})).json();
  assert.ok(running.job_id,JSON.stringify(running));assert.ok(queued.job_id,JSON.stringify(queued));
  const cancelQ=await(await page.request.post(base+'/api/jobs/'+queued.job_id+'/cancel',{data:{}})).json();assert.equal(cancelQ.status,'canceled');
  assert.equal((await json('/api/health')).status,'ready');
  await page.request.post(base+'/api/jobs/'+running.job_id+'/cancel',{data:{}});const canceled=await terminal(running.job_id);assert.equal(canceled.status,'canceled');assert.equal(canceled.result,null);
  check('queued and running acquisition cancellation with responsive health polling');
  await page.reload();await draft();await page.waitForFunction(()=>!document.querySelector('#nav-reconstruct').disabled);await page.click('#nav-reconstruct');
  await page.selectOption('#reconstructions',recid);await page.waitForFunction(id=>document.querySelector('#run-metadata').textContent.includes(id),recid);
  assert.equal((await meta('run-metadata')).snapshot_id,source.snapshot_id);
  await page.setViewportSize({width:540,height:1000});await page.screenshot({path:'/tmp/ct-m2-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.route('**/api/reconstructions/'+recid,route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:{message:'Reconstruction unavailable after restart'}})}));
  await page.selectOption('#reconstructions','');await page.selectOption('#reconstructions',recid);
  await page.waitForFunction(()=>document.querySelector('#reconstruct-status').textContent.includes('unavailable'));
  assert.equal(await page.locator('#reconstruction-results').isVisible(),false);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  check('reload, unavailable records, responsive layout and exclusively local runtime requests');
  const summary={operator:acq.operator,acquisition_seconds:acq.elapsed_seconds,projection_seconds:acq.projection_seconds,noise:noisy.noise,reconstruction:record,
    final_metrics:(await json('/api/reconstructions/'+recid+'/history'))};
  const {writeFile}=await import('node:fs/promises');await writeFile('/tmp/ct-m2-browser-evidence.json',JSON.stringify(summary,null,2));
}finally{await browser.close();}
