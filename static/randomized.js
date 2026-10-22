import {observationChanged,sourceReady,calibratedSource} from './source-calibration.js';
import {runCoverage,restoreCoverage,clearCoverage} from './validation.js';
import {rowPlot} from './validation-presentation.js';
import {api,get,watch} from './ct.js';
import {mapBytes} from './exact-view.js';
const $=id=>document.getElementById(id),num=id=>Number($(id).value),uid=()=>crypto.randomUUID();
let rankResolution=null,prepared=null,preparedSignature=null,preparing=false,finalizing=false;
const preparationSignature=()=>JSON.stringify([...$('rq-form').querySelectorAll('input,select')].map(e=>[e.id,e.value]));
let observations=[],truth=null,pendingObservation=null,base=null,view=null,generation=0,serial=0,signature=null;
const guarded=fn=>async(...args)=>{try{await fn(...args);}catch(e){$('rq-error').textContent=e.message;}};
const formSignature=()=>JSON.stringify([...document.querySelectorAll('#rq-form input,#rq-form select,#rq-final-form input')].map(e=>[e.id,e.type==='checkbox'?e.checked:e.value]));
function options(id,rows,key,label){const el=$(id),old=el.value;el.replaceChildren(new Option('Select a record',''));for(const r of rows)el.add(new Option(label(r),r[key]));if(rows.some(r=>r[key]===old))el.value=old;}
function controls(){
  const automatic=$('rq-mode').value==='automatic';
  $('rq-k-label').hidden=automatic;$('rq-budget-label').hidden=!automatic;
  $('rq-k').disabled=automatic;$('rq-k').required=!automatic;
  $('rq-budget').disabled=!automatic;$('rq-budget').required=automatic;
  const o=observations.find(o=>o.observation_id===$('rq-observation').value);
  if(o&&o.resolution!==rankResolution){$('rq-rank').value=({16:32,32:64,64:128})[o.resolution]??Math.min(128,o.resolution*2);rankResolution=o.resolution;}
  $('rq-context').textContent=o?`${o.resolution} × ${o.resolution}; observation ${o.observation_id.slice(0,8)}; noise SD ${o.noise.sigma}.`:'Select an observation from Measure.';
  $('rq-submit').disabled=!o||!sourceReady()||preparing;
  $('rq-finalize').disabled=finalizing||preparing||!prepared||preparedSignature!==preparationSignature()||prepared.eta_calibration?.eta==null;
  if(prepared&&preparedSignature!==preparationSignature()){
    prepared=null;preparedSignature=null;$('rq-eta').value='';delete $('rq-eta').dataset.help;
    $('rq-preparation-status').textContent='Stage 1 settings changed. Prepare again to select a matching K and η.';
  }
  observationChanged(controls);
  if(base)$('rq-mismatch').textContent=signature===formSignature()?'Displayed result matches submitted controls.':'Displayed result keeps its captured settings. Recalculate to apply current controls.';
}
async function lists(){
  const r=await get('/api/randomized-analyses');
  options('rq-analyses',r.randomized_analyses.filter(a=>a.workflow_phase!=='prepared'),'analysis_id',a=>`${a.analysis_id.slice(0,8)} · ${a.status} · K ${a.selected_k??'none'} · r ${a.configuration.rank}`);controls();
}
window.addEventListener('ct-records',e=>{observations=e.detail.observations;options('rq-observation',observations,'observation_id',o=>`${o.observation_id.slice(0,8)} · ${o.resolution}² · σ ${o.noise.sigma}`);if(pendingObservation&&observations.some(o=>o.observation_id===pendingObservation))$('rq-observation').value=pendingObservation;lists().catch(e=>$('rq-error').textContent=e.message);});
window.addEventListener('ct-observation-selected',e=>{pendingObservation=e.detail;if($('rq-observation').value!==e.detail){$('rq-observation').value=e.detail;generation++;}controls();});
$('rq-observation').onchange=()=>{pendingObservation=$('rq-observation').value;};
$('rq-final-form').addEventListener('input',controls);
$('rq-form').addEventListener('input',()=>{generation++;controls();});$('rq-form').addEventListener('change',()=>{generation++;controls();});
function request(){
  const body={request_id:uid(),rank:num('rq-rank'),oversampling:num('rq-p'),sketch_seed:num('rq-sketch'),probe_seed:num('rq-probe'),probe_count:num('rq-count'),block_size:num('rq-block'),epsilon:num('rq-epsilon'),delta:num('rq-delta'),backend:'matrix_free',reference:true,
    eta:null};
  return ['/api/randomized-preparations',{...body,observation_id:$('rq-observation').value,step_policy:'power_iteration_half_v1',alpha:0.05,settings_origin:'unverified',declaration:'User-specified settings; noise independence has not been established.',
    source:calibratedSource(),
    selection:$('rq-mode').value==='automatic'?{mode:'automatic',budget:num('rq-budget')}:{mode:'fixed',iterations:num('rq-k'),provenance:'imported_selection_unknown'}}];
}
$('rq-form').onsubmit=guarded(async e=>{
  e.preventDefault();const [url,body]=request(),g=++generation,sig=preparationSignature();
  prepared=null;preparedSignature=null;preparing=true;$('rq-eta').value='';delete $('rq-eta').dataset.help;
  $('rq-error').textContent='';$('rq-results').hidden=true;clearCoverage();controls();
  $('rq-preparation-status').textContent='Selecting K and calibrating η…';
  try{
    const {result:job}=await api(url,body);const done=await watch(job);await lists();
    if(g!==generation)return;
    if(done.status!=='completed')throw Error(done.error?.message||`Preparation ${done.status}.`);
    const a=await get('/api/randomized-analyses/'+done.result.analysis_id);if(g!==generation)return;
    if(a.status!=='completed'){$('rq-preparation-status').textContent='No stopping crossing within the budget. No K or η was substituted.';return;}
    prepared=a;preparedSignature=sig;const c=a.eta_calibration;
    $('rq-eta').value=c?.eta??'';
    $('rq-eta').dataset.help=`At K = ${a.iteration}: retained-bias norm ${c.actual_retained_bias_norm.toPrecision(6)}; omitted-bias norm ${c.actual_omitted_bias_norm.toPrecision(6)}. η = omitted / retained. Retained bound ${c.retained_bound?.toPrecision(6)??'unavailable'}: ${c.retained_assumption_supported?'passes':'fails or unresolved'}. Drawing-calibrated; not a theoretical guarantee.`;
    $('rq-preparation-status').textContent=`K = ${a.iteration}; η = ${c.eta==null?'unavailable ('+c.ratio_status+')':c.eta.toPrecision(6)}. Retained-bias bound ${c.retained_assumption_supported?'passes this drawing check.':'fails or is unresolved; calibrated η does not repair it. Intervals remain exploratory.'}`;
  }finally{preparing=false;if(g!==generation)$('rq-preparation-status').textContent='Settings changed during preparation. Prepare again for current settings.';else if(!prepared&&$('rq-preparation-status').textContent.startsWith('Selecting'))$('rq-preparation-status').textContent='Preparation did not complete. See the error or job status, then retry.';controls();}
});
$('rq-final-form').onsubmit=guarded(async e=>{
  e.preventDefault();if(!prepared||preparedSignature!==preparationSignature()||prepared.eta_calibration?.eta==null)throw Error('Complete stage 1 with current settings first.');
  const captured=prepared,g=generation,alpha=num('rq-alpha'),trials=num('rq-trials'),sig=formSignature();finalizing=true;controls();$('rq-error').textContent='';
  try{
    const {result:job}=await api(`/api/randomized-analyses/${captured.analysis_id}/finalize`,{request_id:uid(),alpha});
    const done=await watch(job);if(done.status!=='completed')throw Error(done.error?.message||`Intervals ${done.status}.`);
    const a=await get('/api/randomized-analyses/'+done.result.analysis_id);await lists();
    if(g!==generation||prepared!==captured)return;
    await show(a.analysis_id,sig);await runCoverage(a,trials);
  }finally{finalizing=false;controls();}
});
async function show(aid,sig=null){
  const token=++serial;truth=null;base=view=null;$('rq-results').hidden=true;clearCoverage();
  if(!aid)return;
  const a=await get('/api/randomized-analyses/'+aid);
  if(a.workflow_phase==='prepared')return;
  const snapshot=await get('/api/snapshots/'+a.snapshot_id);if(token!==serial)return;
  truth=snapshot.pixels;base=view=a;signature=sig;$('rq-analyses').value=aid;$('rq-results').hidden=false;
  $('rq-main-row').value=Math.floor(a.shape[0]/2);$('rq-main-row').max=a.shape[0]-1;
  renderPrimary();controls();$('rq-status').textContent=`Saved reconstruction · ${a.status}`;
  await restoreCoverage(a);
}
$('rq-analyses').onchange=guarded(()=>show($('rq-analyses').value));$('rq-refresh').onclick=guarded(lists);

function renderPrimary(){
  const svg=$('rq-row-plot'),canvas=$('rq-main-image');canvas.hidden=!view?.center;canvas.parentElement.querySelector('.density-scale').hidden=!view?.center;
  $('rq-main-row').disabled=!view?.center;
  if(!view?.center){rowPlot(svg,null,null,0,0);$('rq-primary-context').textContent=`No reconstruction selected: ${base?.status??'unavailable'}.`;$('rq-primary-status').textContent='Automatic stopping did not find a crossing within the search budget. No cap reconstruction or interval is substituted.';return;}
  const n=view.shape[0],row=num('rq-main-row');if(!Number.isInteger(row)||row<0||row>=n)throw Error(`Choose a row from 0 to ${n-1}.`);
  const small=document.createElement('canvas');small.width=small.height=n;small.getContext('2d').putImageData(new ImageData(mapBytes(view.center,[0,1]),n,n),0,0);
  const ctx=canvas.getContext('2d');ctx.imageSmoothingEnabled=false;ctx.clearRect(0,0,512,512);ctx.drawImage(small,0,0,512,512);ctx.strokeStyle='#2878b8';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(0,(row+.5)*512/n);ctx.lineTo(512,(row+.5)*512/n);ctx.stroke();
  svg.style.height='420px';
  rowPlot(svg,truth,view,n,row,view.configuration.alpha);
  $('rq-primary-context').textContent=`Observation ${base.observation_id.slice(0,8)} · actual K = ${view.iteration} · selected K = ${base.selected_k} · rank ${base.configuration.rank} · noise SD ${base.configuration.sigma}. ${view.iteration!==base.selected_k?'Exploratory iteration inspection.':''}`;
  $('rq-primary-status').textContent=view.intervals_available?view.coverage_interpretation:`Confidence intervals unavailable: ${view.intervals_unavailable_reason||'declare justified source parameters and omitted-bias allowance'}.`;
}
$('rq-main-row').onchange=guarded(renderPrimary);
$('rq-main-image').onclick=guarded(e=>{if(!view?.center)return;const box=e.currentTarget.getBoundingClientRect();$('rq-main-row').value=Math.max(0,Math.min(view.shape[0]-1,Math.floor((e.clientY-box.top)*view.shape[0]/box.height)));renderPrimary();});
controls();

// Keep the chart dimensions aligned with the square image after layout changes.
let primaryWidth=0;
new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(width>0&&width!==primaryWidth){primaryWidth=width;if(view?.center)renderPrimary();}}).observe(document.querySelector('.rq-primary-grid'));
