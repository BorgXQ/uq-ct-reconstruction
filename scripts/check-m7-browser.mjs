import assert from 'node:assert/strict';
import {chromium} from 'playwright';
const base=process.env.TEST_URL||'http://127.0.0.1:5017';
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const get=async p=>(await page.request.get(base+p)).json();
async function terminal(id){for(let i=0;i<3000;i++){const j=await get('/api/jobs/'+id);if(['completed','failed','canceled'].includes(j.status))return j;await page.waitForTimeout(25);}throw Error('Timeout');}
async function action(button,path){const response=page.waitForResponse(r=>r.url()===base+path&&r.request().method()==='POST');await page.click(button);const j=await(await response).json();assert.ok(j.job_id,JSON.stringify(j));const done=await terminal(j.job_id);assert.equal(done.status,'completed',JSON.stringify(done));return done.result;}
async function prepare(){const r=await action('#rq-submit','/api/randomized-preparations');await page.waitForFunction(()=>!document.querySelector('#rq-submit').disabled&&!document.querySelector('#rq-preparation-status').textContent.startsWith('Selecting'));return get('/api/randomized-analyses/'+r.analysis_id);}
async function finalize(p){const r=await action('#rq-finalize',`/api/randomized-analyses/${p.analysis_id}/finalize`);await page.waitForFunction(id=>document.querySelector('#rq-analyses').value===id&&!document.querySelector('#rq-results').hidden,r.analysis_id);return get('/api/randomized-analyses/'+r.analysis_id);}
async function reconstruct(){return finalize(await prepare());}

try{
 await page.goto(base);await page.waitForFunction(()=>document.querySelector('#preview-status').textContent.includes('Backend conversion complete'));
 assert.equal(await page.locator('nav button').count(),3);assert.match(await page.locator('#nav-uq').innerText(),/03 Reconstruct/);
 for(const id of ['reconstruct-workspace','reconstruction-form','uq-form','rq-route','new-randomized-observation'])assert.equal(await page.locator('#'+id).count(),0);
 await page.selectOption('#resolution','16');await page.click('[data-tool=circle]');const b=await page.locator('#editor').boundingBox();await page.mouse.move(b.x+b.width*.5,b.y+b.height*.5);await page.mouse.down();await page.mouse.move(b.x+b.width*.7,b.y+b.height*.5);await page.mouse.up();
 await page.click('#freeze');await page.waitForFunction(()=>document.querySelector('#freeze-status').textContent.startsWith('Frozen snapshot')&&!document.querySelector('#freeze').disabled);
 await page.click('#nav-measure');await page.fill('#n-angles','24');await page.fill('#n-detectors','24');await page.fill('#sigma','0.02');const scan=await action('#generate-scan','/api/acquisitions');await page.waitForFunction(()=>!document.querySelector('#scan-results').hidden);
 await page.click('#nav-uq');assert.equal(await page.inputValue('#rq-observation'),scan.observation_id);
 await page.waitForFunction(()=>document.querySelector('#rq-source-status').textContent.includes('Using calibrated M'),{},{timeout:120000});
 assert.equal(await page.locator('#rq-calibrated-M').getAttribute('readonly'),'');
 const initialM=Number(await page.inputValue('#rq-calibrated-M'));assert.ok(initialM>0);
 assert.equal(await page.locator('#rq-M').getAttribute('readonly'),null);
 await page.fill('#rq-M','6');await page.waitForFunction(()=>document.querySelector('#rq-source-status').textContent.includes('Using manual M = 6'));
 await page.fill('#rq-M','');await page.waitForFunction(()=>document.querySelector('#rq-source-status').textContent.includes('Using calibrated M'));
 await page.fill('#rq-nu','0.6');
 assert.equal(await page.locator('#rq-submit').isDisabled(),true);assert.equal(await page.inputValue('#rq-calibrated-M'),'');
 await page.locator('#rq-nu').press('Tab');await page.waitForFunction(()=>document.querySelector('#rq-source-status').textContent.includes('Using calibrated M'));
 assert.notEqual(Number(await page.inputValue('#rq-calibrated-M')),initialM);
 await page.fill('#rq-nu','0.5');await page.locator('#rq-nu').press('Tab');await page.waitForFunction(()=>document.querySelector('#rq-source-status').textContent.includes('Using calibrated M'));
 assert.equal(Number(await page.inputValue('#rq-calibrated-M')),initialM);
 assert.equal(await page.locator('#optional-validation,#vl-form,#rq-map-grid,#rq-inspect').count(),0);
 assert.equal(await page.inputValue('#rq-trials'),'100');
 assert.equal(await page.inputValue('#rq-mode'),'automatic');assert.equal(await page.inputValue('#rq-budget'),'1500');
 assert.equal(await page.inputValue('#rq-eta'),'');assert.equal(await page.locator('#rq-eta').getAttribute('readonly'),'');assert.equal(await page.inputValue('#rq-rank'),'32');
 await page.locator('[data-parameter="rq-trials"]').hover();await page.waitForSelector('#parameter-tooltip:not([hidden])');await page.keyboard.press('Escape');
 await page.selectOption('#rq-mode','fixed');await page.fill('#rq-k','5');await page.fill('#rq-trials','3');
 await page.fill('#rq-rank','4');await page.fill('#rq-p','4');await page.fill('#rq-count','4');
 const studyCount=(await get('/api/validation-studies')).studies.length;
 const preparation=await prepare();assert.equal(await page.locator('#rq-results').isVisible(),false);
 assert.equal((await get('/api/validation-studies')).studies.length,studyCount);
 assert.equal(Number(await page.inputValue('#rq-eta')),preparation.eta_calibration.eta);
 await page.locator('[data-parameter="rq-eta"]').hover();await page.waitForSelector('#parameter-tooltip:not([hidden])');
 assert.match(await page.locator('#parameter-tooltip').textContent(),/retained-bias norm.*omitted-bias norm/);await page.keyboard.press('Escape');
 await page.fill('#rq-alpha','0.1');assert.equal(await page.locator('#rq-finalize').isDisabled(),false);
 await page.fill('#rq-alpha','0.05');
 const a=await finalize(preparation);assert.equal(a.reconstruction_id,preparation.reconstruction_id);assert.deepEqual(a.center,preparation.center);assert.equal(a.iteration,5);assert.equal(a.step.step_policy,'power_iteration_half_v1');assert.ok(a.step.power_relative_residual<=1e-8);
 await page.waitForFunction(()=>document.querySelector('#coverage-status').textContent.includes('completed · 3/3'),{},{timeout:120000});
 assert.equal(await page.locator('#coverage-image').isVisible(),true);assert.match(await page.locator('#coverage-title').textContent(),/, 3 trials\)/);
 const studies=(await get('/api/validation-studies')).studies;const entry=studies.find(s=>s.plan.template_id===a.analysis_id);assert.ok(entry);
 const study=await get('/api/validation-studies/'+entry.study_id+'?arrays=1');
 assert.deepEqual(study.step,a.step);assert.equal(study.plan.iterations,a.iteration);assert.equal(study.plan.trials,3);assert.equal(study.plan.protocol,'fixed_algorithm');assert.equal(study.plan.comparison,'common_k');assert.deepEqual(study.plan.methods,['corrected']);
 assert.equal(study.configuration.rank,a.configuration.rank);assert.deepEqual(study.configuration.source,a.configuration.source);
 const values=study.maps.corrected.coverage;
 const mean=(100*values.reduce((s,v)=>s+v,0)/values.length).toFixed(2);
 assert.match(await page.locator('#coverage-title').textContent(),new RegExp(mean.replace('.','\\.')+'%'));
 await page.waitForFunction(()=>document.querySelector('#rq-row-plot').data?.length===4);
 const centers=await page.locator('#rq-row-plot').evaluate(el=>el.data.find(t=>t.name==='Landweber mean').y);
 assert.deepEqual(centers,a.center.slice(128,144));
 const imageBox=await page.locator('#rq-main-image').boundingBox(),coverageBox=await page.locator('#coverage-image').boundingBox(),plotBox=await page.locator('#rq-row-plot').boundingBox();
 assert.ok(Math.abs(imageBox.width-coverageBox.width)<1);assert.ok(Math.abs(imageBox.y-coverageBox.y)<1);
 assert.ok(plotBox.y>=imageBox.y+imageBox.height);assert.ok(plotBox.width>imageBox.width*2);
 assert.equal(await page.locator('#coverage-status').isVisible(),false);
 assert.equal(await page.locator('#coverage-scale').isVisible(),true);
 assert.equal(await page.locator('#coverage-scale .density-ticks span').count(),11);
 const corner=await page.locator('#coverage-image').evaluate(el=>Array.from(el.getContext('2d').getImageData(0,0,1,1).data));
 const {coverageBytes}=await import('../static/coverage-colors.js');assert.deepEqual(corner,Array.from(coverageBytes([values[0]])));
 assert.equal(await page.locator('#coverage-diagnostics').evaluate(el=>el.open),false);
 await page.locator('#coverage-diagnostics > summary').click();await page.waitForSelector('#coverage-bias-plot canvas');
 assert.equal(await page.locator('#coverage-variance-plot canvas').count(),1);
 assert.match(await page.locator('#coverage-diagnostic-summary').textContent(),/3 returned reconstructions/);
 await page.locator('#coverage-bias-plot canvas').scrollIntoViewIfNeeded();
 const biasCanvas=await page.locator('#coverage-bias-plot canvas').boundingBox();await page.mouse.move(biasCanvas.x+1,biasCanvas.y+1);
 assert.match(await page.locator('#coverage-bias-plot .diagnostic-pixel').textContent(),new RegExp((study.maps.corrected.mean[0]-study.truth[0]).toPrecision(4).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
 await page.locator('#coverage-variance-plot canvas').scrollIntoViewIfNeeded();
 const varCanvas=await page.locator('#coverage-variance-plot canvas').boundingBox();await page.mouse.move(varCanvas.x+1,varCanvas.y+1);
 assert.ok((await page.locator('#coverage-variance-plot .diagnostic-pixel').textContent()).includes((study.maps.corrected.empirical_variance[0]-a.variance[0]).toPrecision(4)));
 await page.locator('#coverage-diagnostics > summary').click();
 console.log('PASS automatic coverage at captured K, matching settings, whole-image mean and original-observation plot');
 // Restoring a reconstruction loads its study without submitting another run.
 await page.selectOption('#rq-analyses','');await page.selectOption('#rq-analyses',a.analysis_id);
 await page.waitForFunction(()=>document.querySelector('#coverage-status').textContent.includes('completed · 3/3'));
 assert.equal((await get('/api/validation-studies')).studies.length,studies.length);
 // Changing stage-one settings invalidates eta and stage two, without submitting validation.
 await page.fill('#rq-k','6');assert.equal(await page.inputValue('#rq-eta'),'');assert.equal(await page.locator('#rq-finalize').isDisabled(),true);
 await page.fill('#rq-k','5');assert.equal(await page.locator('#rq-finalize').isDisabled(),true);
 // No crossing is not silently replaced by the iteration cap.
 await page.selectOption('#rq-mode','automatic');await page.fill('#rq-budget','1');
 const noCross=await prepare();assert.equal(noCross.status,'criterion_not_met');assert.equal(await page.locator('#rq-results').isVisible(),false);
 assert.equal(await page.locator('#rq-finalize').isDisabled(),true);
 assert.equal((await get('/api/validation-studies')).studies.length,studies.length);
 // Validation failure leaves the original reconstruction and intervals available.
 await page.route('**/api/validation-studies',route=>route.request().method()==='POST'?route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:{message:'Test work limit exceeded'}})}):route.continue());
 await page.selectOption('#rq-mode','fixed');const failed=await reconstruct();
 await page.waitForFunction(()=>document.querySelector('#coverage-status').textContent.includes('Test work limit exceeded'));
 assert.equal(await page.locator('#rq-main-image').isVisible(),true);assert.equal(await page.locator('#rq-row-plot').evaluate(el=>el.data.some(t=>t.fill==='tonexty')),true);
 await page.unroute('**/api/validation-studies');
 await page.selectOption('#rq-analyses',a.analysis_id);await page.waitForFunction(()=>document.querySelector('#coverage-status').textContent.includes('completed · 3/3'));
 await page.setViewportSize({width:540,height:900});await page.waitForTimeout(500);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);console.log('PASS saved coverage, staged preparation and invalidation, no crossing, validation failure, mobile layout and no browser errors');
}finally{await browser.close();}
