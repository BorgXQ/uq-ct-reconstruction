import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {writeFile} from 'node:fs/promises';
const base=process.env.TEST_URL||'http://127.0.0.1:5015';
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050}});
const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const get=async p=>(await page.request.get(base+p)).json();
async function post(p,data){const r=await page.request.post(base+p,{data});const j=await r.json();assert.ok(r.ok(),JSON.stringify(j));return j;}
async function terminal(id){for(let i=0;i<3000;i++){const j=await get('/api/jobs/'+id);if(['completed','canceled','failed'].includes(j.status))return j;await sleep(30);}throw Error('Job timeout');}
async function job(p,data){const j=await post(p,data),d=await terminal(j.job_id);assert.equal(d.status,'completed',JSON.stringify(d));return d.result;}
async function action(button,path){await page.evaluate(()=>{window.__m5job=null;if(!window.__m5listener){window.__m5listener=true;window.addEventListener('ct-job-submitted',e=>window.__m5job=e.detail);}});await page.click('#'+button);await page.waitForFunction(()=>window.__m5job?.job_id);const j=await page.evaluate(()=>window.__m5job);const d=await terminal(j.job_id);assert.equal(d.status,'completed',JSON.stringify(d));return d.result;}
try{
 const raw=Buffer.alloc(512*512);for(let row=100;row<380;row++)for(let col=120;col<360;col++)raw[row*512+col]=160;
 const snap=await post('/api/snapshots',{request_id:'browser-m5',editor_document_id:'browser-m5',editor_revision:1,source_side:512,source_encoding:'grayscale_u8_base64',source_data:raw.toString('base64'),numerical_resolution:16,preprocessing_version:'block_area_v1'});
 const scan=await job('/api/acquisitions',{request_id:'scan',snapshot_id:snap.snapshot_id,geometry:{n_angles:16,n_detectors:24,start_degrees:0,end_degrees:180},noise:{sigma:.02,seed:3}});
 const template=await job('/api/randomized-analyses',{request_id:'template',observation_id:scan.observation_id,source:{nu:.5,M:2,provenance:'Browser test'},alpha:.05,settings_origin:'declared_independent',declaration:'Browser fixture',step_policy:'safe_induced_norm_v1',selection:{mode:'fixed',iterations:10,provenance:'fixed_predeclared'},rank:2,oversampling:4,sketch_seed:17,probe_seed:23,probe_count:4,block_size:2,epsilon:.2,delta:.01,eta:{value:.1,provenance:'Declared test'},backend:'matrix_free',reference:true});
 await page.goto(base);await page.click('#nav-uq');await page.locator('#optional-validation > summary').click();await page.click('#vl-refresh');await page.selectOption('#vl-template','randomized:'+template.analysis_id);
 await page.fill('#vl-trials','6');await page.fill('#vl-k','10');await page.check('[name="vl-method"][value="exact"]');await page.check('[name="vl-method"][value="retained"]');
 await page.click('#vl-preflight');await page.waitForFunction(()=>document.querySelector('#vl-preflight-info').textContent.includes('workspace_bytes'));
 const fixed=await action('vl-run','/api/validation-studies');await page.waitForFunction(s=>document.querySelector('#vl-studies').value===s&&!document.querySelector('#vl-results').hidden,fixed.study_id);
 await page.waitForSelector('#vl-row-plot [data-role=interval-band]');
 assert.equal(await page.locator('#vl-result-diagnostics').getAttribute('open'),null);
 assert.equal(await page.locator('#vl-main-reconstruction canvas').count(),1);
 assert.match(await page.locator('#vl-ellipse-summary').innerText(),/Mean coverage inside ellipse/);
 await page.fill('#vl-main-row','7');await page.locator('#vl-main-row').press('Tab');
 assert.match(await page.locator('#vl-row-plot').textContent(),/row 7/);
 await page.getByText('Coverage region',{exact:true}).click();
 await page.fill('#vl-ellipse-x','0.5');await page.locator('#vl-ellipse-x').press('Tab');
 const result=await get('/api/validation-studies/'+fixed.study_id+'?arrays=1');
 const {ellipseCoverage}=await import('../static/validation-presentation.js');
 const expected=ellipseCoverage(result.maps.corrected.coverage,16,.5,.9);
 assert.match(await page.locator('#vl-ellipse-summary').innerText(),new RegExp((100*expected.mean).toFixed(2)+'%'));
 await page.locator('#vl-result-diagnostics > summary').click();
 assert.match(await page.locator('#vl-interpretation').innerText(),/6\/6/);assert.equal(await page.locator('#vl-maps canvas').count(),5);
 await page.fill('#vl-col','3');await page.click('#vl-pixel');await page.waitForFunction(()=>document.querySelector('#vl-pixel-info').textContent.includes('Not retained'));
 await page.locator('#vl-maps canvas').first().focus();await page.keyboard.press('ArrowRight');assert.equal(await page.inputValue('#vl-col'),'4');
 await page.selectOption('#vl-protocol','all_random_sources');await page.selectOption('#vl-comparison','own_stopping');await page.fill('#vl-budget','150');await page.fill('#vl-trials','3');await page.uncheck('[name="vl-method"][value="exact"]');
 const fresh=await action('vl-run','/api/validation-studies');await page.waitForFunction(s=>document.querySelector('#vl-studies').value===s,fresh.study_id);
 const freshr=await get('/api/validation-studies/'+fresh.study_id+'/trials');assert.equal(new Set(freshr.trials.map(t=>t.factor_id)).size,3);
 await page.selectOption('#vl-comparison','common_k');await page.fill('#vl-trials','2');await page.getByText('Parameter studies',{exact:true}).click();await page.fill('#vl-values','2,4');
 await page.click('#vl-grid-review');await page.waitForFunction(()=>!document.querySelector('#vl-grid-run').disabled);await action('vl-grid-run','/api/parameter-studies');await page.waitForFunction(()=>document.querySelector('#vl-grid-links').children.length===2);
 await page.selectOption('#vl-studies',fixed.study_id);await page.waitForFunction(()=>document.querySelector('#vl-interpretation').textContent.includes('6/6'));
 await page.getByText('Artifacts & replay',{exact:true}).click();
 const exported=await action('vl-export','/api/exports');const rawBundle=await(await page.request.get(base+exported.download)).body();await writeFile('/tmp/ct-m5-browser.zip',rawBundle);
 await page.setInputFiles('#vl-import-file','/tmp/ct-m5-browser.zip');const restored=await action('vl-import','/api/imports');await page.waitForFunction(s=>document.querySelector('#vl-studies').value===s,restored.study_id);
 const replay=await action('vl-replay-trial',`/api/validation-studies/${restored.study_id}/inspection-requests`);assert.equal(replay.status,'agreement');
 await page.screenshot({path:'/tmp/ct-m5-desktop.png',fullPage:true});await page.setViewportSize({width:540,height:1000});await page.screenshot({path:'/tmp/ct-m5-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);console.log('M5 browser passed: fixed/fresh trials, common/own K, grid, real maps, keyboard pixel, numerical export/restore/replay, responsive/local assets.');
}finally{await browser.close();}
