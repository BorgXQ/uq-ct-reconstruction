import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {writeFile} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const base=process.env.TEST_URL||'http://127.0.0.1:5013';
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}});
const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const get=async path=>(await page.request.get(base+path)).json();
const post=async(path,data)=>{const r=await page.request.post(base+path,{data});const j=await r.json();assert.ok(r.ok(),JSON.stringify(j));return j;};
const field=async(id,value)=>{await page.fill('#'+id,String(value));await page.locator('#'+id).press('Tab');};
const check=s=>console.log('PASS '+s);
async function terminal(id){for(let i=0;i<6000;i++){const j=await get('/api/jobs/'+id);if(['completed','canceled','failed'].includes(j.status))return j;await sleep(10);}throw Error('Job timeout');}
async function action(button,path){const response=page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST');await page.click('#'+button);const j=await(await response).json();assert.ok(j.job_id,JSON.stringify(j));const done=await terminal(j.job_id);assert.equal(done.status,'completed',JSON.stringify(done));return done.result;}
async function analysis(){const path=await page.locator('#uq-route').inputValue()==='attach'?`/api/reconstructions/${await page.locator('#uq-run').inputValue()}/exact-analyses`:'/api/exact-analyses';const refs=await action('uq-submit',path);await page.waitForFunction(id=>document.querySelector('#uq-analyses').value===id,refs.analysis_id);await page.waitForFunction(()=>!document.querySelector('#uq-results').hidden);if(!await page.locator('#uq-results > details').evaluate(el=>el.open))await page.locator('#uq-results > details > summary').click();return get('/api/exact-analyses/'+refs.analysis_id);}
async function selected(id){await page.selectOption('#uq-analyses',id);await page.waitForFunction(id=>document.querySelector('#uq-summary').textContent.includes(id),id);}
try{
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#preview-status').textContent.includes('Backend conversion complete'));
  await page.selectOption('#resolution','16');await field('shade',192);await page.click('[data-tool=circle]');
  await page.locator('#editor').scrollIntoViewIfNeeded();let b=await page.locator('#editor').boundingBox();
  await page.mouse.move(b.x+b.width*.35,b.y+b.height*.35);await page.mouse.down();await page.mouse.move(b.x+b.width*.53,b.y+b.height*.35);await page.mouse.up();
  await field('shade',83);await page.click('[data-tool=square]');await page.locator('#editor').scrollIntoViewIfNeeded();b=await page.locator('#editor').boundingBox();
  await page.mouse.move(b.x+b.width*.65,b.y+b.height*.63);await page.mouse.down();await page.mouse.move(b.x+b.width*.81,b.y+b.height*.79);await page.mouse.up();
  await page.click('#freeze');await page.waitForFunction(()=>document.querySelector('#freeze-status').textContent.startsWith('Frozen snapshot'));
  const snapshot=JSON.parse(await page.locator('#metadata').textContent());
  await page.waitForFunction(()=>!document.querySelector('#nav-measure').disabled);await page.click('#nav-measure');await field('sigma',.01);await field('noise-seed',19);
  const scan=await action('generate-scan','/api/acquisitions');await page.waitForFunction(id=>document.querySelector('#measure-observation').value===id,scan.observation_id);
  await page.click('#nav-reconstruct');await field('iterations',201);
  const rec=await action('run-reconstruction','/api/reconstructions');await page.waitForFunction(()=>document.querySelector('#iteration-status').dataset.iteration==='201');
  await page.click('#analyze-reconstruction');assert.equal(await page.locator('#uq-k').inputValue(),'201');
  const stochastic=await analysis();assert.equal(stochastic.source_condition_status,'not_supplied');assert.equal(stochastic.lower,null);
  assert.equal(stochastic.step.omega,(await get('/api/reconstructions/'+rec.reconstruction_id)).configuration.omega);
  assert.match(await page.locator('#uq-legend-total_half_width').textContent(),/source_missing/);
  assert.match(await page.locator('#uq-legend-extra').textContent(),/pixel sd/);
  assert.equal(await page.locator('#uq-map-extra').getAttribute('width'),'16');
  const canvasBox=await page.locator('#uq-map-extra').boundingBox();
  assert.ok(canvasBox.width>250&&canvasBox.height>250,'Numerical maps must fill their display panels');
  check('draw → frozen observation → existing M2 reconstruction → source-free stochastic SD with preserved step');
  await page.check('#uq-source-enabled');await field('uq-nu',.5);await field('uq-M',6);await field('uq-source-provenance','Unverified example declaration; not fitted to this drawing.');
  const full=await analysis();assert.ok(full.lower);assert.equal(full.coverage_interpretation,'Exploratory interval');
  await field('uq-row',7);await field('uq-col',4);await page.click('#uq-pixel');const index=7*16+4;
  const text=await page.locator('#uq-pixel-info').textContent();assert.ok(text.includes(full.lower[index].toPrecision(17)));assert.ok(text.includes(full.noise_half_width[index].toPrecision(17)));
  await page.selectOption('#uq-map-choice','lower');assert.match(await page.locator('#uq-legend-extra').textContent(),/lower/);
  assert.equal(await page.locator('#uq-bound option[value=global]').evaluate(o=>o.disabled),true,await page.locator('#uq-input-context').textContent());
  await page.selectOption('#uq-bound','safe_global');const global=await analysis();assert.equal(global.bias_allowance.method,'safe_global');
  assert.ok(global.bias_allowance.value>=full.bias_allowance.value);
  await selected(full.analysis_id);
  const diag=await action('uq-diagnose',`/api/exact-analyses/${full.analysis_id}/diagnostics`);
  await page.waitForFunction(()=>document.querySelector('#uq-diagnostic-info').textContent.includes('actual_bias_norm'));
  const diagnostic=await get('/api/exact-diagnostics/'+diag.diagnostic_id);assert.deepEqual((await get('/api/exact-analyses/'+full.analysis_id)).lower,full.lower);
  assert.match(await page.locator('#uq-diagnostic-status').textContent(),/not repeated-measurement coverage/);
  await page.selectOption('#uq-map-choice','actual_bias');assert.equal(await page.locator('#uq-map-extra').isVisible(),true);
  await page.screenshot({path:'/tmp/ct-m3-desktop.png',fullPage:true});
  check('source-conditioned components/endpoints, compatible global bound, actual bias and scoped source diagnostics');
  // Two matched bundles race: delayed first response must not overwrite second.
  let release,entered;const held=new Promise(r=>release=r),arrived=new Promise(r=>entered=r);let count=0;
  await page.route('**/api/exact-views/*',async route=>{const response=await route.fetch();if(++count===1){entered();await held;}await route.fulfill({response});});
  await field('uq-inspect-k',3);await page.click('#uq-inspect');await arrived;
  await field('uq-inspect-k',7);await page.click('#uq-inspect');await page.waitForFunction(()=>document.querySelector('#uq-inspection-status').dataset.iteration==='7');release();await sleep(250);
  assert.equal(await page.locator('#uq-inspection-status').getAttribute('data-iteration'),'7');assert.match(await page.locator('#uq-inspection-status').textContent(),/selected K: 201/);
  assert.match(await page.locator('#uq-pixel-info').textContent(),/retrospective_inspection/);await page.unroute('**/api/exact-views/*');
  await field('uq-inspect-k',0);await page.click('#uq-inspect');await page.waitForFunction(()=>document.querySelector('#uq-inspection-status').dataset.iteration==='0');
  assert.match(await page.locator('#uq-legend-total_half_width').textContent(),/initialization/);await page.click('#uq-selected');
  check('matched center/uncertainty inspection, initialization state and stale-response rejection without changing selected K');
  await page.selectOption('#uq-route','new');await page.selectOption('#uq-observation',scan.observation_id);await page.selectOption('#uq-step','exact_spectral_half');await page.selectOption('#uq-bound','spectral');
  await page.selectOption('#uq-mode','automatic');await field('uq-budget',1000);await field('uq-M',.05);
  const automatic=await analysis();assert.equal(automatic.selection.status,'criterion_met');assert.ok(automatic.selected_k>1);
  const selectedRun=await get('/api/reconstructions/'+automatic.reconstruction_id);assert.equal(selectedRun.completed_iterations,automatic.selected_k);assert.deepEqual(selectedRun.pixels,automatic.center);
  const stopping=await get('/api/exact-analyses/'+automatic.analysis_id+'/stopping-history');assert.equal(stopping.history.length,automatic.selected_k);assert.ok(stopping.history.at(-1).crossed);assert.ok(stopping.history.slice(0,-1).every(h=>!h.crossed));
  assert.equal(await page.locator('#uq-stopping-chart polyline').count(),2);
  await field('uq-M',100);await field('uq-budget',2);const no=await analysis();assert.equal(no.status,'criterion_not_met');assert.equal(no.selected_k,null);assert.equal(await page.locator('#uq-maps').isVisible(),false);
  const cap=await action('uq-cap',`/api/exact-analyses/${no.analysis_id}/cap-reconstruction`);await page.waitForFunction(id=>document.querySelector('#uq-analyses').value===id,cap.analysis_id);
  assert.match(await page.locator('#uq-interpretation').textContent(),/Exploratory/);
  check('first integer crossing binds full Landweber, real stopping plot, no-crossing and explicit exploratory cap');
  const noiseJob=await post(`/api/acquisitions/${scan.acquisition_id}/observations`,{request_id:'m3-seed',noise:{sigma:.01,seed:20}});const noise=await terminal(noiseJob.job_id);
  await page.click('#uq-refresh');await page.waitForFunction(id=>Array.from(document.querySelector('#uq-observation').options).some(o=>o.value===id),noise.result.observation_id);
  await page.selectOption('#uq-observation',noise.result.observation_id);await field('uq-M',.05);await field('uq-budget',1000);
  const other=await analysis();assert.equal(other.selected_k,automatic.selected_k);assert.deepEqual(other.total_half_width,automatic.total_half_width);assert.notDeepEqual(other.center,automatic.center);
  assert.deepEqual((await get(`/api/exact-analyses/${other.analysis_id}/stopping-history`)).history,stopping.history);
  check('noise seed changes centers but not covariance, widths or deterministic selection');
  // A delayed submission reply cannot attach its completed result to changed controls.
  let releasePost,postEntered;const heldPost=new Promise(r=>releasePost=r),postArrived=new Promise(r=>postEntered=r);
  await page.route('**/api/exact-analyses',async route=>{if(route.request().method()==='POST'){const response=await route.fetch();postEntered();await heldPost;await route.fulfill({response});}else await route.continue();});
  await page.click('#uq-submit');await postArrived;await field('uq-alpha',.01);releasePost();await sleep(500);
  assert.equal(await page.locator('#uq-analyses').inputValue(),other.analysis_id);assert.match(await page.locator('#uq-mismatch').textContent(),/unchanged/);await page.unroute('**/api/exact-analyses');
  // Zero noise remains zero variance, with a bias-only interval and no joint inverse.
  const zeroJob=await post(`/api/acquisitions/${scan.acquisition_id}/observations`,{request_id:'m3-zero',noise:{sigma:0,seed:19}});const zero=await terminal(zeroJob.job_id);
  await page.click('#uq-refresh');await page.waitForFunction(id=>Array.from(document.querySelector('#uq-observation').options).some(o=>o.value===id),zero.result.observation_id);
  await page.selectOption('#uq-observation',zero.result.observation_id);await page.selectOption('#uq-mode','fixed');await field('uq-k',20);
  const zeror=await analysis();assert.ok(zeror.variance.every(v=>v===0));assert.ok(zeror.total_half_width.every(v=>v===zeror.bias_allowance.value));assert.equal(zeror.joint_region.reason,'zero_noise');
  await page.route('**/api/exact-analyses',async route=>{
    if(route.request().method()==='POST')await route.fulfill({status:507,contentType:'application/json',body:JSON.stringify({error:{code:'resource_limit',message:'Exact workspace capacity exceeded'}})});
    else await route.continue();
  });
  await page.click('#uq-submit');await page.waitForFunction(()=>document.querySelector('#ct-error').textContent.includes('capacity exceeded'));
  assert.equal(await page.locator('#uq-analyses').inputValue(),zeror.analysis_id);await page.unroute('**/api/exact-analyses');
  // A real longer build occupies the existing single worker while an exact plan is canceled.
  const blocking=await post('/api/acquisitions',{request_id:'block-for-cancel',snapshot_id:snapshot.snapshot_id,
    geometry:{n_angles:360,n_detectors:256,start_degrees:0,end_degrees:180},noise:{sigma:0,seed:0}});
  const canceledReply=page.waitForResponse(r=>r.url()===base+'/api/exact-analyses'&&r.request().method()==='POST');
  await page.click('#uq-submit');const cancelJob=await(await canceledReply).json();assert.ok(cancelJob.job_id,JSON.stringify(cancelJob));
  await page.locator(`[data-job-id="${cancelJob.job_id}"] button`).click();
  const canceled=await terminal(cancelJob.job_id);assert.equal(canceled.status,'canceled');assert.equal(canceled.result,null);
  assert.equal((await get('/api/health')).status,'ready');await post(`/api/jobs/${blocking.job_id}/cancel`,{});await terminal(blocking.job_id);
  assert.equal(await page.locator('#uq-analyses').inputValue(),zeror.analysis_id);
  check('resource refusal preserves displayed analysis; exact job cancellation uses the responsive shared worker');
  await page.reload();await page.waitForFunction(()=>!document.querySelector('#nav-uq').disabled);await page.click('#nav-uq');await selected(full.analysis_id);
  await page.waitForFunction(()=>document.querySelector('#uq-diagnostic-info').textContent.includes('actual_bias_norm'));
  await page.setViewportSize({width:540,height:1000});await page.screenshot({path:'/tmp/ct-m3-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.route('**/api/exact-analyses/'+full.analysis_id,route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:{message:'Analysis unavailable'}})}));
  await page.selectOption('#uq-analyses','');await page.selectOption('#uq-analyses',full.analysis_id);await page.waitForFunction(()=>document.querySelector('#uq-status').textContent.includes('unavailable'));
  assert.equal(await page.locator('#uq-results').isVisible(),false);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  check('prospective settings cannot relabel old maps; zero noise, persisted diagnostic association, mobile and unavailable states');
  await writeFile('/tmp/ct-m3-browser-evidence.json',JSON.stringify({snapshot:snapshot.snapshot_id,full,diagnostic,automatic,stopping},null,2));
}finally{await browser.close();}
