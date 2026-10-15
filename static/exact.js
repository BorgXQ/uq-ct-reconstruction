import {api,get,watch,refresh} from './ct.js';
import {limits,mapBytes,stoppingPlot} from './exact-view.js';
const $=id=>document.getElementById(id),id=()=>crypto.randomUUID(),fmt=x=>x===null||x===undefined?'unavailable':Number(x).toPrecision(17);
let observations=[],runs=[],base=null,view=null,truth=null,diagnostic=null,lastInspected=null,lastObservation=null,history=[];
let generation=0,selectionSerial=0,inspectionSerial=0,diagnosticSerial=0,listSerial=0,inspectionJob=null,acceptedForm=null;
function error(e){$('ct-error').hidden=!e;$('ct-error').textContent=e?.message||e||'';}
const safe=f=>async e=>{try{await f(e);}catch(e){error(e);}};
function num(name){const field=$(name);if(field.value.trim()===''||!field.checkValidity())throw Error(`Enter a valid ${field.labels?.[0]?.textContent||name}.`);return Number(field.value);}
function formSignature(){return JSON.stringify(Array.from($('uq-form').querySelectorAll('input,select'),x=>[x.id,x.type==='checkbox'?x.checked:x.value]));}
function options(name,records,key,label){const select=$(name),value=select.value;select.replaceChildren(new Option('Select a record',''));for(const r of records)select.add(new Option(label(r),r[key]));if(records.some(r=>r[key]===value))select.value=value;}
function controls(){
  if(observations.length&&$('uq-state').textContent==='not_ready')$('uq-state').textContent='ready';
  const attached=$('uq-route').value==='attach',auto=!attached&&$('uq-mode').value==='automatic',source=$('uq-source-enabled').checked;
  $('uq-run-label').hidden=!attached;$('uq-observation-label').hidden=attached;$('uq-new-settings').hidden=attached;
  $('uq-k').disabled=auto;$('uq-k').min=attached?0:1;
  $('uq-budget').disabled=$('uq-c0').disabled=!auto;
  $('uq-provenance').disabled=auto;
  $('uq-source-fields').hidden=!source;
  for(const key of ['uq-nu','uq-M','uq-source-provenance']){$(key).disabled=!source;$(key).required=source;}
  const run=runs.find(r=>r.reconstruction_id===$('uq-run').value),obs=observations.find(o=>o.observation_id===(attached?run?.observation_id:$('uq-observation').value));
  const policy=attached?run?.configuration.step_policy:$('uq-step').value;
  for(const option of $('uq-bound').options)option.disabled=(option.value==='global'&&policy!=='exact_spectral_half')||(option.value==='safe_global'&&policy!=='safe_induced_norm_v1');
  $('uq-policy-help').textContent=`${attached?'Existing step is preserved.':'A new run uses its resolved step.'} Global bounds require their matching half-step policy; the spectral bound supports stable fixed steps. No source declaration means SD only.`;
  $('uq-input-context').textContent=obs?`Observation ${obs.observation_id} · ${obs.resolution} × ${obs.resolution} · known σ ${obs.noise.sigma}, seed ${obs.noise.seed} · operator ${obs.operator_id} · ${attached?`actual ω ${fmt(run.configuration.omega)} (${policy}) · completed K ${run.completed_iterations}`:policy}. ${obs.resolution===64?'Exact mode unavailable at 64×64; M2 remains usable.':''}`:'Select a compatible record.';
  $('uq-submit').disabled=!obs||obs.resolution===64||(attached&&run?.status!=='completed')||$('uq-bound').selectedOptions[0]?.disabled;
  if($('uq-bound').selectedOptions[0]?.disabled)$('uq-policy-help').textContent+=' Choose a compatible bias policy to enable calculation.';
}
function stale(){if(base)$('uq-mismatch').textContent=acceptedForm===formSignature()?'Displayed result matches the submitted form.':'Captured result is unchanged; current form/selection may differ. Recalculate to apply prospective settings.';}
function request(){
  const attached=$('uq-route').value==='attach';
  const source=$('uq-source-enabled').checked?{nu:num('uq-nu'),M:num('uq-M'),provenance:$('uq-source-provenance').value.trim()}:null;
  const body={request_id:id(),source,bound_method:$('uq-bound').value,alpha:num('uq-alpha'),settings_origin:$('uq-origin').value,declaration:$('uq-declaration').value};
  if(attached){body.iteration=num('uq-k');body.selection_provenance=$('uq-provenance').value;return [`/api/reconstructions/${$('uq-run').value}/exact-analyses`,body];}
  body.observation_id=$('uq-observation').value;body.step_policy=$('uq-step').value;
  body.selection=$('uq-mode').value==='automatic'?{mode:'automatic',budget:num('uq-budget'),c0:num('uq-c0')}:{mode:'fixed',iterations:num('uq-k'),provenance:$('uq-provenance').value};
  return ['/api/exact-analyses',body];
}
async function lists(){
  const serial=++listSerial;const data=await get('/api/exact-analyses');if(serial!==listSerial)return;
  options('uq-analyses',data.exact_analyses,'analysis_id',r=>`${r.analysis_id.slice(0,8)} · ${r.status} · selected K ${r.selected_k??'none'} · σ ${r.configuration.sigma}`);
}
window.addEventListener('ct-records',e=>{observations=e.detail.observations;runs=e.detail.runs;
  options('uq-observation',observations,'observation_id',o=>`${o.observation_id.slice(0,8)} · ${o.resolution}² · σ ${o.noise.sigma}`);
  options('uq-run',runs,'reconstruction_id',r=>`${r.reconstruction_id.slice(0,8)} · ${r.status} · K ${r.completed_iterations} · ${r.configuration.step_policy}`);
  controls();lists().catch(error);
});
window.addEventListener('ct-inspected',e=>lastInspected=e.detail);
window.addEventListener('ct-observation-selected',e=>lastObservation=e.detail);
$('new-exact-observation').onclick=()=>{
  if(!lastObservation)return;$('uq-route').value='new';$('uq-observation').value=lastObservation;
  generation++;controls();stale();$('nav-uq').click();
};
window.addEventListener('ct-draft',()=>{if(base)$('uq-mismatch').textContent='Live editor changed or refreshed; analysis remains tied to its captured snapshot. Current form changes require a new calculation.';});
$('analyze-reconstruction').onclick=()=>{
  if(!lastInspected)return;$('uq-route').value='attach';$('uq-run').value=lastInspected.reconstruction_id;$('uq-k').value=lastInspected.iteration;
  $('uq-provenance').value='retrospective_inspection';generation++;controls();stale();$('nav-uq').click();
};
$('uq-form').addEventListener('input',()=>{generation++;controls();stale();});
$('uq-form').addEventListener('change',()=>{generation++;controls();stale();});
$('uq-run').addEventListener('change',()=>{const r=runs.find(x=>x.reconstruction_id===$('uq-run').value);if(r)$('uq-k').value=r.completed_iterations;controls();});
$('uq-form').onsubmit=safe(async e=>{
  e.preventDefault();const [url,body]=request(),gen=++generation,serial=selectionSerial,signature=formSignature();
  const {result:job}=await api(url,body);error('');$('uq-status').textContent=`Analysis submitted as job ${job.job_id}. Captured settings will not change.`;
  watch(job,async done=>{await lists();if(gen===generation&&serial===selectionSerial&&done.result?.analysis_id)await show(done.result.analysis_id,signature);}).catch(error);
});
async function show(aid,signature=null){
  const serial=++selectionSerial;++inspectionSerial;++diagnosticSerial;view=null;diagnostic=null;truth=null;
  if(inspectionJob)api(`/api/jobs/${inspectionJob}/cancel`,{}).catch(()=>{});inspectionJob=null;
  $('uq-results').hidden=true;$('uq-status').textContent=aid?'Loading captured analysis…':'No analysis selected.';
  if(!aid){base=null;return;}
  try{
    const [a,h]=await Promise.all([get('/api/exact-analyses/'+aid),get(`/api/exact-analyses/${aid}/stopping-history`)]);
    const t=await get('/api/snapshots/'+a.snapshot_id);
    if(serial!==selectionSerial)return;
    base=a;view=a;truth=t;history=h.history;acceptedForm=signature;$('uq-analyses').value=aid;$('uq-results').hidden=false;
    $('uq-status').textContent=`Captured analysis ${aid} · ${a.status}`;
    $('uq-result-title').textContent=`Full-spectrum baseline · ${a.status}`;
    $('uq-summary').textContent=`Analysis ${aid} · observation ${a.observation_id} · frozen source ${a.snapshot_id} · analysis selected K ${a.selected_k??'none'} · σ ${a.configuration.sigma} · ω ${fmt(a.step.omega)} (${a.step.step_policy}) · ${a.elapsed_seconds.toFixed(3)} s.`;
    $('uq-cap').hidden=a.status!=='criterion_not_met';$('uq-inspection-controls').hidden=a.status!=='completed';
    $('uq-inspect-k').max=runs.find(r=>r.reconstruction_id===a.reconstruction_id)?.completed_iterations??a.selected_k??0;
    $('uq-inspect-k').value=a.iteration??0;
    $('uq-row').max=$('uq-col').max=a.shape[0]-1;$('uq-row').value=$('uq-col').value=0;
    $('uq-stopping-label').textContent=`${a.selection.status} · ${history.length} candidate(s) evaluated. ${a.status==='criterion_not_met'?'No crossing; the budget is not a selected stop.':a.configuration.selection.mode==='automatic'?`First crossing K = ${a.selected_k??'none'}.`:'Fixed/attached analysis; no stopping search was run.'}`;
    stoppingPlot($('uq-stopping-chart'),history,a.selected_k);
    $('uq-stopping-chart').style.display=history.length?'':'none';
    $('uq-diagnostic-status').textContent='Not requested. Single-run spatial inclusion is not repeated-measurement coverage.';$('uq-diagnostic-info').textContent='';
    render();stale();
    // Restore only diagnostic records explicitly requested previously, scoped to this analysis.
    const ds=await get('/api/exact-diagnostics');if(serial!==selectionSerial)return;
    const d=ds.exact_diagnostics.filter(d=>d.analysis_id===aid).at(-1);
    if(d){const full=await get('/api/exact-diagnostics/'+d.diagnostic_id);if(serial===selectionSerial){diagnostic=full;renderDiagnostic();render();}}
  }catch(e){if(serial===selectionSerial){base=null;view=null;$('uq-results').hidden=true;$('uq-status').textContent='Analysis unavailable in this backend process.';error(e);}}
}
function data(key){
  if(!view?.center)return null;
  if(key==='truth')return truth?.pixels;
  if(['absolute_error','actual_bias','inclusion'].includes(key))return diagnostic?.iteration===view.iteration?diagnostic[key]:null;
  return view[key];
}
function draw(key,name=key){
  const values=data(key),canvas=$('uq-map-'+name),marker=$('uq-marker-'+name),legend=$('uq-legend-'+name),scale=$('uq-scale-'+name);
  scale.hidden=!values;
  if(!values){canvas.hidden=marker.hidden=true;legend.textContent=['absolute_error','actual_bias','inclusion'].includes(key)?'Unavailable: run truth diagnostics for the selected K.':view?.intervals_unavailable_reason||'No matching numerical map.';return;}
  canvas.hidden=marker.hidden=false;
  let range=limits(values);const raw=range;
  if(['truth','center','inclusion'].includes(key))range=[0,1];
  if(key==='lower'||key==='upper')range=limits([...view.lower,...view.upper]);
  if(key==='actual_bias'){const extent=Math.max(Math.abs(range[0]),Math.abs(range[1]));range=[-extent,extent];}
  const n=view.shape[0];canvas.width=canvas.height=n;
  scale.children[0].textContent=range[0].toPrecision(4);scale.children[2].textContent=range[1].toPrecision(4);
  scale.children[1].style.background=range[0]===range[1]?(key==='actual_bias'?'rgb(128,200,128)':'#808080'):key==='actual_bias'?'linear-gradient(to right,rgb(0,0,255),rgb(128,200,128),rgb(255,0,0))':'linear-gradient(to right,#000,#fff)';
  canvas.getContext('2d').putImageData(new ImageData(mapBytes(values,range,{signed:key==='actual_bias'}),n,n),0,0);
  legend.textContent=`${key.replaceAll('_',' ')} · scale ${fmt(range[0])} → ${fmt(range[1])}${raw[0]===raw[1]?` · constant ${fmt(raw[0])}`:''}${['truth','center'].includes(key)?' · fixed [0,1] display; out-of-range raw values saturate.':''}${key==='total_half_width'?` · shared bias contribution ${fmt(view.bias_allowance?.value)}.`:''}`;
}
function render(){
  if(!view)return;
  $('uq-maps').hidden=!view.center;
  $('uq-interpretation').textContent=`${view.coverage_interpretation||view.status}. ${(view.unmet_conditions||[]).join(' ')} Marginal pixelwise formula; not simultaneous whole-image coverage.`;
  $('uq-inspection-status').textContent=`Analysis selected K: ${base.selected_k??'none'} · Inspecting k: ${view.iteration??'none'} · ${view.selection_status||'no selection'}`;
  $('uq-inspection-status').dataset.iteration=view.iteration??'';
  $('uq-diagnose').disabled=base.status!=='completed'||view.iteration!==base.selected_k;
  $('uq-assumptions').textContent=JSON.stringify({conditions:view.conditions,unmet_conditions:view.unmet_conditions,warnings:view.warnings,evidence:diagnostic?.source_check??'not requested'},null,2);
  $('uq-details').textContent=JSON.stringify({analysis_id:base.analysis_id,configuration:base.configuration,step:base.step,spectrum:base.spectrum,
    iteration:view.iteration,trace_covariance:view.trace_covariance,bias_allowance:view.bias_allowance,gaussian_quantile:view.gaussian_quantile},null,2);
  $('uq-joint').textContent=`Joint inverse-covariance region: ${view.joint_region?.reason||'no completed analysis'}. Marginal intervals do not require an inverse.`;
  if(view.center){for(const key of ['truth','center','total_half_width','absolute_error'])draw(key);draw($('uq-map-choice').value,'extra');pixel();}
  hideValidation();renderDiagnostic();
}
function hideValidation(){for(const key of ['truth','absolute_error'])document.querySelector(`[data-uq-card="${key}"]`).hidden=!$('uq-show-truth').checked;}
function pixel(){
  if(!view?.center)return;const row=num('uq-row'),col=num('uq-col'),n=view.shape[0];
  if(!Number.isInteger(row)||!Number.isInteger(col)||row<0||col<0||row>=n||col>=n)throw Error('Pixel outside this analysis grid.');
  const i=row*n+col,b=view.bias_allowance?.value,h=view.total_half_width?.[i];
  let text=`Analysis ${base.analysis_id}\nRun ${view.reconstruction_id} · observation ${view.observation_id}\nOperator ${view.operator_id}\nRow ${row}, column ${col}, index ${i}; center (${fmt(-1+(col+.5)*2/n)}, ${fmt(1-(row+.5)*2/n)})\nActual k ${view.iteration}; selected K ${base.selected_k}; ω ${fmt(view.step.omega)}\nReconstructed density: ${fmt(view.center[i])}\nVariance: ${fmt(view.variance[i])}; modeled SD: ${fmt(view.pixel_sd[i])}\nGaussian quantile: ${fmt(view.gaussian_quantile)}; stochastic half-width: ${fmt(view.noise_half_width[i])}\nBias allowance: ${fmt(b)} (${view.configuration.bound_method}, ${view.source_condition_status})\nTotal half-width: ${fmt(h)}; full width: ${fmt(h===undefined?null:2*h)}\nRaw interval: [${fmt(view.lower?.[i])}, ${fmt(view.upper?.[i])}]\n${view.coverage_interpretation}; ${view.selection_status}`;
  if(diagnostic?.iteration===view.iteration&&$('uq-show-truth').checked)text+=`\nValidation only: truth ${fmt(diagnostic.truth[i])}; signed error ${fmt(diagnostic.error[i])}; actual bias ${fmt(diagnostic.actual_bias[i])}; inclusion ${diagnostic.inclusion?Boolean(diagnostic.inclusion[i]):'unavailable'}.`;
  $('uq-pixel-info').textContent=text;
  for(const name of ['truth','center','total_half_width','absolute_error','extra']){const ctx=$('uq-marker-'+name).getContext('2d'),s=512/n;ctx.clearRect(0,0,512,512);ctx.strokeStyle='#ee912c';ctx.lineWidth=2;ctx.strokeRect(col*s+1,row*s+1,s-2,s-2);}
}
async function inspect(){
  if(!base)return;const k=num('uq-inspect-k'),serial=++inspectionSerial,aid=base.analysis_id,sel=selectionSerial;
  if(inspectionJob)api(`/api/jobs/${inspectionJob}/cancel`,{}).catch(()=>{});
  const valid=()=>serial===inspectionSerial&&sel===selectionSerial&&base?.analysis_id===aid;
  $('uq-inspection-status').textContent=`Computing matched image and uncertainty for k ${k}; displayed bundle still belongs to k ${view?.iteration}.`;
  const {result:job}=await api(`/api/exact-analyses/${aid}/inspection-requests`,{request_id:id(),iteration:k});
  if(!valid()){api(`/api/jobs/${job.job_id}/cancel`,{}).catch(()=>{});watch(job).catch(error);return;}
  inspectionJob=job.job_id;const done=await watch(job);if(!valid())return;inspectionJob=null;
  if(done.status!=='completed')throw Error(`Inspection ${done.status}; saved analysis unchanged.`);
  const v=await get('/api/exact-views/'+done.result.view_id);if(!valid())return;
  if(v.analysis_id!==aid||v.configuration_hash!==base.configuration_hash||v.iteration!==k)throw Error('Inspection identity mismatch.');
  view=v;render();
}
function renderDiagnostic(){
  if(!diagnostic)return;
  $('uq-diagnostic-status').textContent=`${diagnostic.effective_interpretation} Single-run spatial inclusion ${diagnostic.inclusion_fraction===null?'unavailable':(100*diagnostic.inclusion_fraction).toFixed(2)+'%'}. This is not repeated-measurement coverage.`;
  $('uq-diagnostic-info').textContent=JSON.stringify({analysis_id:diagnostic.analysis_id,iteration:diagnostic.iteration,
    actual_bias_norm:diagnostic.actual_bias_norm,selected_allowance:base.bias_allowance?.value,bias_bound_supported:diagnostic.bias_bound_supported,
    rmse:diagnostic.rmse,relative_error:diagnostic.relative_error,source_check:diagnostic.source_check},null,2);
  if(diagnostic.source_check.status==='violated'||diagnostic.bias_bound_supported===false)$('uq-interpretation').textContent=diagnostic.effective_interpretation;
}
$('uq-diagnose').onclick=safe(async()=>{
  if(!base)return;const aid=base.analysis_id,sel=selectionSerial,serial=++diagnosticSerial;
  const {result:job}=await api(`/api/exact-analyses/${aid}/diagnostics`,{request_id:id()});
  const done=await watch(job);if(sel!==selectionSerial||serial!==diagnosticSerial)return;
  if(done.status!=='completed')throw Error(`Diagnostics ${done.status}; inference remains unchanged.`);
  const d=await get('/api/exact-diagnostics/'+done.result.diagnostic_id);
  if(sel!==selectionSerial||serial!==diagnosticSerial||d.analysis_id!==base?.analysis_id)return;
  diagnostic=d;render();renderDiagnostic();
});
$('uq-cap').onclick=safe(async()=>{
  const aid=base.analysis_id,sel=selectionSerial,gen=generation;
  const {result:job}=await api(`/api/exact-analyses/${aid}/cap-reconstruction`,{request_id:id()});
  watch(job,async done=>{await lists();if(sel===selectionSerial&&gen===generation&&done.status==='completed')await show(done.result.analysis_id);}).catch(error);
});
$('uq-analyses').onchange=safe(()=>show($('uq-analyses').value));
$('uq-refresh').onclick=safe(async()=>{await refresh();await lists();if(base&&!Array.from($('uq-analyses').options).some(o=>o.value===base.analysis_id))await show(base.analysis_id);});
$('uq-inspect').onclick=safe(inspect);$('uq-inspect-k').onchange=()=>{++inspectionSerial;};
$('uq-selected').onclick=safe(async()=>{++inspectionSerial;view=base;$('uq-inspect-k').value=base.selected_k;render();renderDiagnostic();});
$('uq-map-choice').onchange=()=>{if(view?.center){draw($('uq-map-choice').value,'extra');pixel();}};
$('uq-pixel').onclick=safe(pixel);$('uq-show-truth').onchange=()=>{hideValidation();if(['actual_bias','inclusion'].includes($('uq-map-choice').value)&&!$('uq-show-truth').checked)$('uq-map-choice').value='pixel_sd';draw($('uq-map-choice').value,'extra');pixel();};
for(const name of ['truth','center','total_half_width','absolute_error','extra'])$('uq-marker-'+name).onclick=safe(e=>{
  if(!view?.center)return;const r=e.target.getBoundingClientRect(),n=view.shape[0];
  $('uq-row').value=Math.min(n-1,Math.max(0,Math.floor((e.clientY-r.top)*n/r.height)));
  $('uq-col').value=Math.min(n-1,Math.max(0,Math.floor((e.clientX-r.left)*n/r.width)));pixel();
});
controls();lists().catch(error);
