import assert from 'node:assert/strict';
import {chromium} from 'playwright';
const base=process.env.TEST_URL||'http://127.0.0.1:5017';
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const get=async p=>(await page.request.get(base+p)).json();
async function terminal(id){for(let i=0;i<3000;i++){const j=await get('/api/jobs/'+id);if(['completed','failed','canceled'].includes(j.status))return j;await page.waitForTimeout(25);}throw Error('Timeout');}
async function action(button,path){const response=page.waitForResponse(r=>r.url()===base+path&&r.request().method()==='POST');await page.click(button);const j=await(await response).json();assert.ok(j.job_id,JSON.stringify(j));const done=await terminal(j.job_id);assert.equal(done.status,'completed',JSON.stringify(done));return done.result;}
async function reconstruct(){const r=await action('#rq-submit','/api/randomized-analyses');await page.waitForFunction(id=>document.querySelector('#rq-analyses').value===id&&!document.querySelector('#rq-results').hidden,r.analysis_id);return get('/api/randomized-analyses/'+r.analysis_id);}
try{
 const initialStudies=(await get('/api/validation-studies')).studies.length;
 await page.goto(base);await page.waitForFunction(()=>document.querySelector('#preview-status').textContent.includes('Backend conversion complete'));
 assert.equal(await page.locator('nav button').count(),3);assert.match(await page.locator('#nav-uq').innerText(),/03 Reconstruct/);
 for(const id of ['reconstruct-workspace','reconstruction-form','uq-form','rq-route','new-randomized-observation'])assert.equal(await page.locator('#'+id).count(),0);
 await page.selectOption('#resolution','16');await page.click('[data-tool=circle]');const b=await page.locator('#editor').boundingBox();await page.mouse.move(b.x+b.width*.5,b.y+b.height*.5);await page.mouse.down();await page.mouse.move(b.x+b.width*.7,b.y+b.height*.5);await page.mouse.up();
 await page.click('#freeze');await page.waitForFunction(()=>document.querySelector('#freeze-status').textContent.startsWith('Frozen snapshot')&&!document.querySelector('#freeze').disabled);
 await page.click('#nav-measure');await page.fill('#n-angles','24');await page.fill('#n-detectors','24');await page.fill('#sigma','0.02');const scan=await action('#generate-scan','/api/acquisitions');await page.waitForFunction(()=>!document.querySelector('#scan-results').hidden);
 const jobsBefore=(await get('/api/jobs')).jobs.length;await page.click('#nav-uq');assert.equal(await page.inputValue('#rq-observation'),scan.observation_id);assert.equal((await get('/api/jobs')).jobs.length,jobsBefore);
 assert.equal(await page.locator('#optional-validation').evaluate(e=>e.open),false);
 assert.equal(await page.inputValue('#rq-mode'),'automatic');assert.equal(await page.inputValue('#rq-budget'),'1500');
 assert.equal(await page.locator('#rq-k-label').isVisible(),false);assert.equal(await page.locator('#rq-budget-label').isVisible(),true);
 assert.equal(await page.inputValue('#rq-rank'),'32');assert.equal(await page.inputValue('#rq-nu'),'0.5');assert.equal(await page.inputValue('#rq-M'),'8');assert.equal(await page.inputValue('#rq-alpha'),'0.05');
 for(const id of ['rq-step','rq-source','rq-source-provenance','rq-origin','rq-declaration','rq-eta-provenance','rq-backend','rq-reference'])assert.equal(await page.locator('#'+id).count(),0);
 await page.locator('[data-parameter="rq-mode"]').hover();await page.waitForSelector('#parameter-tooltip:not([hidden])');assert.match(await page.locator('#parameter-tooltip').textContent(),/first K/);await page.keyboard.press('Escape');assert.equal(await page.locator('#parameter-tooltip').isVisible(),false);
 // Check resolution-dependent rank defaults, and preserve edits on refresh.
 const rankObservations=[];
 for(const n of [32,64]){
   const response=await page.request.post(base+'/api/snapshots',{data:{request_id:'rank-'+n,editor_document_id:'rank-default-test',editor_revision:1,source_side:512,source_encoding:'grayscale_u8_base64',source_data:Buffer.alloc(512*512,128).toString('base64'),numerical_resolution:n,preprocessing_version:'block_area_v1'}});
   const snapshot=await response.json();assert.ok(response.ok(),JSON.stringify(snapshot));
   const submitted=await page.request.post(base+'/api/acquisitions',{data:{request_id:'rank-scan-'+n,snapshot_id:snapshot.snapshot_id,geometry:{n_angles:2,n_detectors:3,start_degrees:0,end_degrees:180},noise:{sigma:0,seed:0}}});
   const accepted=await submitted.json();const done=await terminal(accepted.job_id);assert.equal(done.status,'completed');rankObservations.push(done.result.observation_id);
 }
 await page.evaluate(()=>document.querySelector('#ct-refresh').click());await page.waitForFunction(id=>[...document.querySelector('#rq-observation').options].some(o=>o.value===id),rankObservations[1]);
 for(const [index,rank] of [[0,'64'],[1,'128']]){await page.selectOption('#rq-observation',rankObservations[index]);assert.equal(await page.inputValue('#rq-rank'),rank);}
 await page.locator('#rq-approx-options > summary').click();await page.fill('#rq-rank','77');await page.click('#rq-refresh');assert.equal(await page.inputValue('#rq-rank'),'77');await page.locator('#rq-approx-options > summary').click();
 await page.selectOption('#rq-observation',scan.observation_id);assert.equal(await page.inputValue('#rq-rank'),'32');

 await page.locator('#rq-source-options > summary').click();await page.fill('#rq-nu','0.5');await page.fill('#rq-M','2');
 await page.locator('#rq-approx-options > summary').click();await page.fill('#rq-rank','4');await page.fill('#rq-p','4');await page.fill('#rq-count','4');await page.fill('#rq-eta','0.1');await page.selectOption('#rq-mode','fixed');assert.equal(await page.locator('#rq-budget-label').isVisible(),false);assert.equal(await page.inputValue('#rq-k'),'500');await page.fill('#rq-k','20');
 const a=await reconstruct();assert.equal(a.configuration.step_policy,'safe_induced_norm_v1');assert.equal(a.configuration.backend,'matrix_free');assert.equal(a.configuration.reference,true);assert.equal(a.configuration.settings_origin,'unverified');await page.waitForSelector('#rq-row-plot .js-fill');assert.deepEqual(await page.locator('#rq-row-plot .legendtext').allTextContents(),['Truth','Landweber mean','95% Confidence interval']);assert.equal(await page.locator('#rq-primary .density-ticks span').count(),11);assert.equal(await page.locator('#rq-row-caption').count(),0);const imageHeight=(await page.locator('#rq-main-image').boundingBox()).height,plotHeight=(await page.locator('#rq-row-plot').boundingBox()).height;assert.ok(Math.abs(imageHeight-plotHeight)<1);assert.equal(await page.locator('#rq-main-image').isVisible(),true);assert.equal(await page.locator('#rq-results > details').evaluate(e=>e.open),false);
 assert.match(await page.locator('#rq-primary-context').innerText(),/actual K = 20/);assert.equal((await get('/api/validation-studies')).studies.length,initialStudies);
 await page.fill('#rq-main-row','5');await page.locator('#rq-main-row').press('Tab');
 await page.waitForFunction(()=>document.querySelector('#rq-row-plot').layout?.xaxis.title.text.includes('row 5'));
 const traces=await page.locator('#rq-row-plot').evaluate(el=>el.data);
 assert.deepEqual(traces.find(t=>t.meta.role==='trial-center').y,a.center.slice(80,96));
 assert.deepEqual(traces.find(t=>t.meta.role==='interval-lower').y,a.lower.slice(80,96));
 assert.deepEqual(traces.find(t=>t.meta.role==='interval-upper').y,a.upper.slice(80,96));
 assert.deepEqual(traces.find(t=>t.meta.role==='truth').y,(await get('/api/snapshots/'+a.snapshot_id)).pixels.slice(80,96));
 // Plotly exposes interactive hover values without changing the raw trace data.
 await page.locator('#rq-row-plot').evaluate(el=>window.Plotly.Fx.hover(el,[{curveNumber:3,pointNumber:4}]));
 assert.match(await page.locator('#rq-row-plot .hoverlayer').textContent(),/Landweber mean/);
 await page.locator('#rq-results > details > summary').click();await page.fill('#rq-inspect-k','7');await action('#rq-inspect',`/api/randomized-analyses/${a.analysis_id}/inspection-requests`);await page.waitForFunction(()=>document.querySelector('#rq-primary-context').textContent.includes('actual K = 7'));
 await page.waitForFunction(()=>document.querySelector('#vl-k').value==='7');
 await page.click('#rq-selected');await page.waitForFunction(()=>document.querySelector('#rq-primary-context').textContent.includes('actual K = 20'));
 await page.locator('#optional-validation > summary').click();await page.waitForFunction(id=>document.querySelector('#vl-template').value==='randomized:'+id,a.analysis_id);assert.equal(await page.inputValue('#vl-k'),'20');await page.fill('#vl-trials','3');const study=await action('#vl-run','/api/validation-studies');await page.waitForFunction(id=>document.querySelector('#vl-studies').value===id,study.study_id);await page.waitForSelector('#vl-main-coverage canvas');assert.match(await page.locator('#vl-ellipse-summary').innerText(),/3\/3/);assert.match(await page.locator('#rq-primary-context').innerText(),/actual K = 20/);
 await page.fill('#rq-eta','');const missing=await reconstruct();assert.equal(missing.lower,null);assert.equal(await page.locator('#rq-row-plot .js-fill').count(),0);assert.match(await page.locator('#rq-primary-status').innerText(),/unavailable/);
 await page.selectOption('#rq-mode','automatic');await page.fill('#rq-budget','1');await page.fill('#rq-M','100');const no=await reconstruct();assert.equal(no.selected_k,null);assert.equal(await page.locator('#rq-main-image').isVisible(),false);assert.equal(await page.locator('#rq-row-plot .js-fill').count(),0);
 await page.selectOption('#rq-analyses',a.analysis_id);await page.waitForSelector('#rq-row-plot .js-fill');await page.locator('#rq-primary').screenshot({path:'/tmp/ct-m7-results.png'});
 await page.setViewportSize({width:540,height:1000});await page.waitForFunction(()=>{const el=document.querySelector('#rq-row-plot');return Math.abs(el._fullLayout.width-el.clientWidth)<2;});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.reload();await page.click('#nav-uq');await page.selectOption('#rq-analyses',a.analysis_id);await page.waitForSelector('#rq-row-plot .js-fill');assert.deepEqual(errors,[]);
 console.log('M7 passed: three tabs, observation handoff without calculation, immediate single-observation intervals, row/raw-data matching, inspection, optional coverage, missing assumptions, no crossing, reload and mobile.');
}finally{await browser.close();}
