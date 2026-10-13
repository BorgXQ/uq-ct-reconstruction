'use strict';
let uqResult=null, uqPayload=null, uqCurrent=false, uqBusy=false, uqTimer=null;
const uf = value => value===null || value===undefined ? 'unavailable' : Number(value).toPrecision(5);
const uqIds=['uq-policy','uq-nu','uq-level','uq-c0','iterations'];
function uqValid(){return uqIds.every(id=>$(id).reportValidity());}
function uqSettings(){return {k_policy:$('uq-policy').value,mode:'oracle',
  nu:Number($('uq-nu').value),level:Number($('uq-level').value),
  c0:Number($('uq-c0').value),budget:Number($('iterations').value)};}
function uqButtons(locked=busy) {
  const available=projection?.version===version && !!results;
  $('uq-run').disabled=locked||!available;
  $('uq-mc').disabled=locked||!uqCurrent;
  $('uq-use-stop').disabled=locked||!uqCurrent||!uqResult?.stopping?.first;
}
function uqInvalidate() {
  clearTimeout(uqTimer);uqCurrent=false;$('uq-mc-results').hidden=true;
  $('uq-inspector').textContent='No current interval. Run Landweber or update the interval settings.';
  $('uq-components').textContent='';$('uq-interval-status').textContent='';
  $('uq-source').textContent='Source condition has not been checked for the current settings.';
  $('uq-stop-status').textContent='Awaiting current calculation.';$('uq-stop-values').textContent='';
  $('uq-status').textContent='Run Landweber above to calculate confidence intervals.';
  uqButtons();
}
uqIds.forEach(id=>$(id).addEventListener('input',()=>{uqInvalidate();$('uq-status').textContent='Settings changed. Update intervals & stopping rule, or run Landweber again.';}));
window.addEventListener('experiment-edit',uqInvalidate);
window.addEventListener('reconstruction-cleared',uqInvalidate);
window.addEventListener('calculation-busy',e=>uqButtons(e.detail));
['uq-trials','uq-mc-seed'].forEach(id=>$(id).addEventListener('input',()=>{$('uq-mc-results').hidden=true;if(uqResult)uqResult.mc=null;uqInspect();}));
function uqConfig() {
  const snapshot=results.snapshots[Number($('snapshot').value)];
  return {...projection,iterations:snapshot.iteration,...uqSettings()};
}
function uqAccept(result,payload) {
  uqResult=result;uqPayload=payload;uqCurrent=true;
  uqDisplay();$('uq-status').textContent=`Intervals for the ${result.config.n} × ${result.config.n} reconstruction at k = ${result.config.k}. Select a pixel on the reconstruction.`;
}
window.addEventListener('reconstruction-displayed',e=>{
  uqInvalidate();
  const attached=results.uncertainty;
  if(attached && attached.config.k===e.detail.iteration && JSON.stringify(attached.settings)===JSON.stringify(uqSettings())) {
    uqAccept(attached,uqConfig());
  } else {
    $('uq-status').textContent=`Updating intervals for displayed iteration ${e.detail.iteration}…`;
    uqTimer=setTimeout(()=>uqRun(),200);
  }
});
async function uqRun(mc=false) {
  if(busy||uqBusy||!results||projection?.version!==version||!uqValid())return;
  if(mc && (!uqCurrent||!['uq-trials','uq-mc-seed'].every(id=>$(id).reportValidity())))return;
  const payload=mc?{...uqPayload,trials:Number($('uq-trials').value),mc_seed:Number($('uq-mc-seed').value)}:uqConfig();
  uqBusy=true;setBusy(true);
  $('uq-status').textContent=mc?'Running optional Monte Carlo validation…':'Calculating covariance, bias bound, and intervals…';
  try {
    const result=await request(mc?'uq-mc':'uq',payload);uqAccept(result,payload);
    if(mc)$('uq-status').textContent='Intervals unchanged. Optional validation statistics are ready below.';
  } catch(error){$('uq-status').textContent=error.message;} finally {uqBusy=false;setBusy(false);uqButtons();}
}
$('uq-run').onclick=()=>uqRun();
$('uq-mc').onclick=()=>uqRun(true);
$('uq-use-stop').onclick=()=>{if(!uqCurrent||!uqResult.stopping?.first)return;$('iterations').value=uqResult.stopping.first;$('uq-policy').value='fixed';$('reconstruct').onclick();};
function uqDisplay() {
  const r=uqResult,c=r.config;
  $('uq-row').max=$('uq-col').max=c.n-1;
  $('uq-row').value=Math.min(Number($('uq-row').value),c.n-1);$('uq-col').value=Math.min(Number($('uq-col').value),c.n-1);
  $('uq-source').textContent=`${r.source.status.toUpperCase()}: ${r.source.reason} M = ${uf(r.source.M)} (${r.source.oracle?'calibrated from your drawing':'proposed assumption'}).`;
  $('uq-interval-status').textContent=r.interval_reason||`${100*c.level}% marginal confidence, conditional on the source assumption. Calculated from the exact covariance and paper bias bound.`;
  const s=r.stopping;
  $('uq-stop-status').textContent=s?(s.first?`First satisfying iteration: K = ${s.first}.`:`Not reached within K = 1…${c.budget}.`):`Unavailable: ${r.interval_reason}`;
  if(s){
    const v=Math.max(...r.maps.sd.flat()),b=r.bounds.global_bound;
    $('uq-stop-values').textContent=`At displayed K = ${c.k}: B_K = ${uf(b)}, c₀ v_K = ${uf(s.c0*v)} — ${b<=s.c0*v?'condition met':'condition not yet met'}.`;
  } else $('uq-stop-values').textContent='';
  $('uq-mc-results').hidden=!r.mc;
  if(r.mc)uqMcDisplay();uqInspect();uqButtons();
}
function uqInspect() {
  const row=Number($('uq-row').value),col=Number($('uq-col').value);
  if(!Number.isInteger(row)||!Number.isInteger(col)||row<0||col<0)return;
  $('uq-pixel-label').textContent=`(${row}, ${col})`;
  if(!uqResult||!uqCurrent||row>=uqResult.config.n||col>=uqResult.config.n)return;
  const r=uqResult,value=name=>r.maps[name]?.[row]?.[col];
  const interval=r.maps.lower?`${100*r.config.level}% interval [${uf(value('lower'))}, ${uf(value('upper'))}]`:'Confidence interval unavailable.';
  $('uq-inspector').textContent=`Reconstructed value: ${uf(value('reconstruction'))}. ${interval}`;
  $('uq-components').textContent=r.maps.lower?`Half-width = ${uf(value('stochastic'))} (noise allowance) + ${uf(r.bounds.global_bound)} (bias bound) = ${uf(value('half_width'))}.` : '';
  if(r.mc){
    const coverage=r.mc.coverage;
    $('uq-mc-pixel').textContent=`Selected pixel (${row}, ${col}): known truth ${uf(value('truth'))}. `+(coverage?`Empirical coverage ${uf(100*coverage.pixel[row][col])}% ± SE ${uf(100*coverage.pixel_se[row][col])} percentage points (${r.mc.trials} trials).`:'Coverage unavailable without eligible intervals.');
  }
}
$('uq-row').oninput=uqInspect;$('uq-col').oninput=uqInspect;
function selectPixel(e){
  if(!uqCurrent)return;
  const rect=$('reconstruction').getBoundingClientRect(),n=uqResult.config.n;
  $('uq-row').value=Math.max(0,Math.min(n-1,Math.floor((e.clientY-rect.top)/rect.height*n)));
  $('uq-col').value=Math.max(0,Math.min(n-1,Math.floor((e.clientX-rect.left)/rect.width*n)));
  uqInspect();
}
$('reconstruction').onpointermove=selectPixel;$('reconstruction').onclick=selectPixel;
function uqMcDisplay() {
  const mc=uqResult.mc;
  $('uq-mc-summary').textContent=`${mc.trials} independent trials · validation seed ${mc.seed}. Comparing repeated reconstructions with the mathematical predictions for this same K.`;
  const coverage=mc.coverage;
  const rows=[
    ['Mean reconstruction vs predicted mean',`Difference norm ${uf(mc.mean_error_norm)}; predicted RMS ${uf(mc.mean_rmse_prediction)}.`],
    ['Pixel variances vs predicted variances',`Relative discrepancy ${uf(mc.variance_relative_error)}.`],
    ['Mean squared image error',`Observed ${uf(mc.observed_mse)} ± SE ${uf(mc.mse_se)}; predicted ${uf(mc.predicted_mse)}.`],
    ['Average marginal interval coverage',coverage?`${uf(100*coverage.average)}% ± SE ${uf(100*coverage.average_se)} percentage points.`:`Unavailable: ${uqResult.interval_reason}`]
  ];
  $('uq-mc-statistics').replaceChildren();
  for(const [label,value] of rows){const tr=document.createElement('tr');for(const text of [label,value]){const td=document.createElement('td');td.textContent=text;tr.append(td);}$('uq-mc-statistics').append(tr);}
}
