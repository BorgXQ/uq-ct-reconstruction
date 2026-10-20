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
 await page.locator('#rq-source-options > summary').click();await page.check('#rq-source');await page.fill('#rq-nu','0.5');await page.fill('#rq-M','2');await page.fill('#rq-source-provenance','Test assumption');
 await page.locator('#rq-approx-options > summary').click();await page.fill('#rq-rank','4');await page.fill('#rq-p','4');await page.fill('#rq-count','4');await page.fill('#rq-eta','0.1');await page.fill('#rq-eta-provenance','Test assumption');await page.fill('#rq-k','20');
 const a=await reconstruct();await page.waitForSelector('#rq-row-plot [data-role=interval-band]');assert.equal(await page.locator('#rq-main-image').isVisible(),true);assert.equal(await page.locator('#rq-results > details').evaluate(e=>e.open),false);
 assert.match(await page.locator('#rq-primary-context').innerText(),/actual K = 20/);assert.equal((await get('/api/validation-studies')).studies.length,initialStudies);
 await page.fill('#rq-main-row','5');await page.locator('#rq-main-row').press('Tab');
 const points=await page.locator('#rq-row-plot [data-role=trial-center]').getAttribute('points');assert.equal(points.split(' ').length,16);assert.equal(await page.locator('#rq-row-plot [data-role=mean]').count(),0);
 // Compare plotted shape with raw reconstruction, accounting for plot scaling.
 const ys=points.split(' ').map(p=>Number(p.split(',')[1])),band=await page.locator('#rq-row-plot [data-role=interval-band]').getAttribute('points');const bounds=band.split(' ').map(p=>Number(p.split(',')[1]));
 for(let i=0;i<16;i++)assert.ok(Math.abs(ys[i]-(bounds[i]+bounds[31-i])/2)<1e-8);
 const values=[...a.center.slice(80,96),...a.lower.slice(80,96),...a.upper.slice(80,96),...(await get('/api/snapshots/'+a.snapshot_id)).pixels.slice(80,96)];const min=Math.min(...values),max=Math.max(...values),pad=Math.max((max-min)*.08,.01);for(let i=0;i<16;i++)assert.ok(Math.abs(ys[i]-(250-220*(a.center[80+i]-min+pad)/(max-min+2*pad)))<1e-8);
 await page.locator('#rq-results > details > summary').click();await page.fill('#rq-inspect-k','7');await action('#rq-inspect',`/api/randomized-analyses/${a.analysis_id}/inspection-requests`);await page.waitForFunction(()=>document.querySelector('#rq-primary-context').textContent.includes('actual K = 7'));
 await page.waitForFunction(()=>document.querySelector('#vl-k').value==='7');
 await page.click('#rq-selected');await page.waitForFunction(()=>document.querySelector('#rq-primary-context').textContent.includes('actual K = 20'));
 await page.locator('#optional-validation > summary').click();await page.waitForFunction(id=>document.querySelector('#vl-template').value==='randomized:'+id,a.analysis_id);assert.equal(await page.inputValue('#vl-k'),'20');await page.fill('#vl-trials','3');const study=await action('#vl-run','/api/validation-studies');await page.waitForFunction(id=>document.querySelector('#vl-studies').value===id,study.study_id);await page.waitForSelector('#vl-main-coverage canvas');assert.match(await page.locator('#vl-ellipse-summary').innerText(),/3\/3/);assert.match(await page.locator('#rq-primary-context').innerText(),/actual K = 20/);
 await page.fill('#rq-eta','');const missing=await reconstruct();assert.equal(missing.lower,null);assert.equal(await page.locator('#rq-row-plot [data-role=interval-band]').count(),0);assert.match(await page.locator('#rq-primary-status').innerText(),/unavailable/);
 await page.selectOption('#rq-mode','automatic');await page.fill('#rq-budget','1');await page.fill('#rq-M','100');const no=await reconstruct();assert.equal(no.selected_k,null);assert.equal(await page.locator('#rq-main-image').isVisible(),false);assert.equal(await page.locator('#rq-row-plot polygon').count(),0);
 await page.selectOption('#rq-analyses',a.analysis_id);await page.waitForSelector('#rq-row-plot polygon');await page.locator('#rq-primary').screenshot({path:'/tmp/ct-m7-results.png'});
 await page.setViewportSize({width:540,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.reload();await page.click('#nav-uq');await page.selectOption('#rq-analyses',a.analysis_id);await page.waitForSelector('#rq-row-plot polygon');assert.deepEqual(errors,[]);
 console.log('M7 passed: three tabs, observation handoff without calculation, immediate single-observation intervals, row/raw-data matching, inspection, optional coverage, missing assumptions, no crossing, reload and mobile.');
}finally{await browser.close();}
