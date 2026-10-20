import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const base=process.env.TEST_URL||'http://127.0.0.1:5014';
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}});
const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));const get=async p=>(await page.request.get(base+p)).json();
const post=async(p,data)=>{const r=await page.request.post(base+p,{data});const j=await r.json();assert.ok(r.ok(),JSON.stringify(j));return j;};
async function terminal(id){for(let i=0;i<6000;i++){const j=await get('/api/jobs/'+id);if(['completed','canceled','failed'].includes(j.status))return j;await sleep(15);}throw Error('timeout');}
async function job(path,body){const j=await post(path,body),done=await terminal(j.job_id);assert.equal(done.status,'completed',JSON.stringify(done));return done.result;}
const field=async(id,value)=>page.fill('#'+id,String(value));
async function action(id,path){const wait=page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST');await page.click('#'+id);const r=await(await wait).json();assert.ok(r.job_id,JSON.stringify(r));const done=await terminal(r.job_id);assert.equal(done.status,'completed',JSON.stringify(done));return done.result;}
async function run(path){const refs=await action('rq-submit',path);await page.waitForFunction(id=>document.querySelector('#rq-analyses').value===id&&!document.querySelector('#rq-results').hidden,refs.analysis_id);if(!await page.locator('#rq-results > details').evaluate(el=>el.open))await page.locator('#rq-results > details > summary').click();return get('/api/randomized-analyses/'+refs.analysis_id);}
try{
  // Actual image bytes, not a mock output; M1–M3 browser suites cover drawing gestures separately.
  const raw=Buffer.alloc(512*512);for(let y=70;y<250;y++)for(let x=90;x<280;x++)raw[y*512+x]=192;
  for(let y=310;y<410;y++)for(let x=310;x<430;x++)raw[y*512+x]=83;
  const snap=await post('/api/snapshots',{request_id:'m4-source',editor_document_id:'m4-browser',editor_revision:1,source_side:512,source_encoding:'grayscale_u8_base64',source_data:raw.toString('base64'),numerical_resolution:16,preprocessing_version:'block_area_v1'});
  const scan=await job('/api/acquisitions',{request_id:'m4-scan',snapshot_id:snap.snapshot_id,geometry:{n_angles:30,n_detectors:24,start_degrees:0,end_degrees:180},noise:{sigma:.01,seed:19}});
  const exactrefs=await job('/api/exact-analyses',{request_id:'m4-exact',observation_id:scan.observation_id,step_policy:'safe_induced_norm_v1',source:{M:6,nu:.5,provenance:'Unverified fixed example'},bound_method:'spectral',alpha:.05,selection:{mode:'fixed',iterations:30,provenance:'fixed_predeclared'},settings_origin:'declared_independent',declaration:'Fixed script settings'});
  const exact=await get('/api/exact-analyses/'+exactrefs.analysis_id);
  await page.goto(base);await page.waitForFunction(()=>!document.querySelector('#nav-uq').disabled);await page.click('#nav-uq');await page.selectOption('#rq-observation',scan.observation_id);
  await page.locator('#rq-source-options > summary').click();await page.locator('#rq-approx-options > summary').click();
  await page.check('#rq-source');await field('rq-nu',.5);await field('rq-M',6);await field('rq-source-provenance','Unverified fixed example');await field('rq-k',30);
  await page.selectOption('#rq-origin','declared_independent');await page.getByText('Randomization, covariance backend and probability targets',{exact:true}).click();await page.check('#rq-reference');
  await field('rq-rank',16);await field('rq-count',8);
  const stochastic=await run('/api/randomized-analyses');assert.deepEqual(stochastic.center,exact.center);assert.equal(stochastic.lower,null);assert.deepEqual(stochastic.full_variance,exact.variance);
  assert.match(await page.locator('#rq-interpretation').textContent(),/unavailable/);
  await field('rq-eta',.1);await field('rq-eta-provenance','Unverified omitted bias declaration');const full=await run('/api/randomized-analyses');
  assert.ok(full.lower);assert.equal(full.coverage_expression_applicable,false);assert.equal(full.probe_control.control_established,false);
  await field('rq-row',7);await field('rq-col',4);await page.click('#rq-pixel');const text=await page.locator('#rq-pixel-info').textContent();assert.ok(text.includes(full.lower[116].toPrecision(17)));assert.ok(text.includes(full.signed_residual[116].toPrecision(17)));
  const canvas=page.locator('#rq-map-grid canvas').first();assert.ok((await canvas.boundingBox()).width>200);
  for(const key of ['tsvd_omitted_variance','residual_estimation_error','correction_minus_full','lower']){await page.selectOption('#rq-map-choice',key);assert.equal(await page.locator('#rq-extra canvas').count(),2);}
  console.log('PASS common-K exact/TSVD/randomized maps, raw inspector, missing eta, honest probability status');
  const before=await get('/api/randomized-analyses/'+full.analysis_id);
  const diag=await action('rq-diagnose',`/api/randomized-analyses/${full.analysis_id}/diagnostics`);await page.waitForFunction(()=>document.querySelector('#rq-diagnostic-info').textContent.includes('retained_assumption_supported'));
  assert.deepEqual(await get('/api/randomized-analyses/'+full.analysis_id),before);const d=await get('/api/approximation-diagnostics/'+diag.diagnostic_id);assert.equal(d.analysis_id,full.analysis_id);
  console.log('PASS scoped omitted-bias diagnostics preserve inference');
  let release,entered;const held=new Promise(r=>release=r),arrived=new Promise(r=>entered=r);let count=0;
  await page.route('**/api/randomized-views/*',async route=>{const response=await route.fetch();if(++count===1){entered();await held;}await route.fulfill({response});});
  await field('rq-inspect-k',2);await page.click('#rq-inspect');await arrived;
  await field('rq-inspect-k',7);await page.click('#rq-inspect');await page.waitForFunction(()=>document.querySelector('#rq-inspection-status').textContent.includes('inspecting k 7;'));release();await sleep(200);
  assert.match(await page.locator('#rq-inspection-status').textContent(),/inspecting k 7;/);assert.match(await page.locator('#rq-inspection-status').textContent(),/Selected K 30/);await page.unroute('**/api/randomized-views/*');
  await field('rq-inspect-k',0);await action('rq-inspect',`/api/randomized-analyses/${full.analysis_id}/inspection-requests`);await page.waitForFunction(()=>document.querySelector('#rq-inspection-status').textContent.includes('inspecting k 0;'));
  assert.match(await page.locator('#rq-interpretation').textContent(),/unavailable/);
  console.log('PASS stale inspected-k responses and initialization state');
  await page.selectOption('#rq-observation',scan.observation_id);await page.uncheck('#rq-reference');await page.selectOption('#rq-mode','automatic');await field('rq-budget',50);
  await page.check('#rq-source');await field('rq-nu',.5);await field('rq-M',.0001);await field('rq-source-provenance','Predeclared test');await page.selectOption('#rq-origin','declared_independent');
  const automatic=await run('/api/randomized-analyses');assert.equal(automatic.status,'completed');assert.ok(automatic.selected_k>=1);assert.equal(automatic.reference.status,'unavailable');
  const rec=await get('/api/reconstructions/'+automatic.reconstruction_id);assert.deepEqual(rec.pixels,automatic.center);
  await field('rq-alpha',.01);await field('rq-eta',.5);const altered=await run('/api/randomized-analyses');assert.equal(altered.selected_k,automatic.selected_k);assert.notDeepEqual(altered.total_half_width,automatic.total_half_width);
  await field('rq-own-budget',50);const own=await action('rq-own',`/api/randomized-analyses/${altered.analysis_id}/stopping-comparisons`);await page.waitForFunction(()=>document.querySelector('#rq-own-summary').textContent.includes('Shared ω'));
  const ownResult=await get('/api/stopping-comparisons/'+own.comparison_id);assert.equal(ownResult.selections.randomized.selected_k,altered.selected_k);
  console.log('PASS randomized first crossing, full solver center, alpha/eta independence and own-stopping comparison');
  await field('rq-M',100);await field('rq-budget',2);const no=await run('/api/randomized-analyses');assert.equal(no.status,'criterion_not_met');assert.equal(no.center,null);
  await page.selectOption('#rq-analyses',automatic.analysis_id);await page.waitForFunction(id=>document.querySelector('#rq-summary').textContent.includes(id),automatic.observation_id);
  await page.waitForFunction(()=>document.querySelector('#rq-map-grid canvas')!==null);
  await page.locator('#randomized-panel').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/ct-m4-desktop.png'});
  // A changed prospective form cannot attach an old accepted job on arrival.
  let unblock,accepted;const gate=new Promise(r=>unblock=r),acceptedGate=new Promise(r=>accepted=r);
  await page.route('**/api/randomized-analyses',async route=>{if(route.request().method()!=='POST')return route.continue();const response=await route.fetch();accepted();await gate;await route.fulfill({response});});
  const shown=await page.locator('#rq-analyses').inputValue();await page.click('#rq-submit');await acceptedGate;
  await field('rq-eta',2);unblock();await sleep(500);assert.equal(await page.locator('#rq-analyses').inputValue(),shown);
  assert.match(await page.locator('#rq-mismatch').textContent(),/captured settings/);await page.unroute('**/api/randomized-analyses');
  // Backend refusal preserves the selected immutable result and its maps.
  await field('rq-rank',253);const refused=page.waitForResponse(r=>r.url().endsWith('/api/randomized-analyses')&&r.request().method()==='POST');await page.click('#rq-submit');assert.equal((await refused).status(),400);
  assert.equal(await page.locator('#rq-results').isVisible(),true);assert.equal(await page.locator('#rq-analyses').inputValue(),shown);await field('rq-rank',16);
  console.log('PASS prospective form race and invalid-rank refusal preserve old results');
  await page.setViewportSize({width:540,height:960});await page.locator('#rq-results').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/ct-m4-mobile.png'});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.reload();await page.click('#nav-uq');await page.selectOption('#rq-analyses',full.analysis_id);await page.waitForFunction(()=>document.querySelector('#rq-diagnostic-info').textContent.includes('retained_assumption_supported'));
  await page.route('**/api/randomized-analyses/'+full.analysis_id,route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:{message:'Unavailable after restart'}})}));
  await page.selectOption('#rq-analyses','');await page.selectOption('#rq-analyses',full.analysis_id);await page.waitForFunction(()=>document.querySelector('#rq-status').textContent.includes('unavailable'));
  assert.equal(await page.locator('#rq-results').isVisible(),false);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS no crossing, responsive local UI, reload evidence and unavailable-ID handling');
}finally{await browser.close();}
