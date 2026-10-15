import {paintRaster} from './editor.js';
import {sharedRange,drawSinogram,plot} from './ct-view.js';
const $=id=>document.getElementById(id);
let snapshots=[],acquisitions=[],observations=[],runs=[],draftState=null;
let scan=null,run=null,iteration=null,history=null,runTruth=null;
let measureGeneration=0,reconstructGeneration=0,observationSerial=0,runSerial=0,inspectSerial=0,listSerial=0,replayJob=null,inspectionTimer;
const polled=new Set();
function message(error){$('ct-error').hidden=!error;$('ct-error').textContent=error||'';}
async function api(url,data){
  const response=await fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok){const e=new Error(result.error?.message||`Request failed (${response.status}).`);e.code=result.error?.code;throw e;}
  return {result,status:response.status};
}
const get=async url=>(await api(url)).result;
const requestId=()=>crypto.randomUUID();
function numeric(id){const field=$(id);if(field.value.trim()===''||!field.checkValidity())throw new Error(`Enter a valid ${field.labels?.[0]?.textContent||id}.`);return Number(field.value);}
function fillOptions(id,records,key,label){
  const select=$(id),previous=select.value;select.replaceChildren(new Option('Select a record',''));
  for(const item of records)select.add(new Option(label(item),item[key]));
  if(records.some(item=>item[key]===previous))select.value=previous;
}
function availability(){
  $('nav-measure').disabled=!snapshots.length;$('nav-reconstruct').disabled=!observations.length;$('nav-uq').disabled=!observations.length;
  $('uq-nav-state').textContent=observations.length?'ready':'not_ready';
  $('generate-scan').disabled=!$('measure-snapshot').value;
  $('run-reconstruction').disabled=!$('reconstruct-observation').value;
  $('new-observation').disabled=$('new-noise').disabled=!scan;
  if(!$('measure-snapshot').value)$('measure-nav-state').textContent=$('measure-state').textContent='not_ready';
  else if($('measure-nav-state').textContent==='not_ready')$('measure-nav-state').textContent=$('measure-state').textContent='ready';
  if(!observations.length)$('reconstruct-nav-state').textContent=$('reconstruct-state').textContent='not_ready';
  else if($('reconstruct-nav-state').textContent==='not_ready')$('reconstruct-nav-state').textContent=$('reconstruct-state').textContent='ready';
}
function currentGeometry(){return {n_angles:numeric('n-angles'),n_detectors:numeric('n-detectors'),start_degrees:numeric('angle-start'),end_degrees:numeric('angle-end')};}
function currentNoise(){return {sigma:numeric('sigma'),seed:numeric('noise-seed')};}
function scanMismatch(){
  if(!scan)return;
  let changed=true;
  try{
    const g=currentGeometry(),m=scan.acquisition.geometry;
    changed=$('measure-snapshot').value!==scan.acquisition.snapshot_id||g.n_angles!==m.sinogram_shape[0]||g.n_detectors!==m.sinogram_shape[1]||g.start_degrees!==m.range_degrees[0]||g.end_degrees!==m.range_degrees[1]||numeric('sigma')!==scan.observation.noise.sigma||numeric('noise-seed')!==scan.observation.noise.seed;
  }catch{}
  const editorMatch=draftState?.ready ? (draftState.content_hash===scan.truth.content_hash&&draftState.source_hash===scan.truth.source_hash ? 'Matches live editor.' : 'Live editor differs; captured truth is unchanged.') : 'Checking live editor; captured truth is unchanged.';
  $('measure-status').textContent=`Displayed observation ${scan.observation.observation_id.slice(0,8)} · ${changed?'Differs from current form.':'Matches current form.'} ${editorMatch}`;
}
function selectSource(id,resetDetector=true){
  const source=snapshots.find(s=>s.snapshot_id===id);
  $('measure-snapshot').value=source?id:'';measureGeneration++;
  if(source){
    if(resetDetector)$('n-detectors').value=Math.ceil(Math.sqrt(2)*source.resolution)+1;
    $('source-context').textContent=`Selected frozen image ${source.snapshot_id} · ${source.resolution} × ${source.resolution} · content ${source.content_hash}`;
  }else $('source-context').textContent='Select a frozen image. Historical images remain valid acquisition inputs.';
  availability();scanMismatch();
}
async function refresh(){
  const serial=++listSerial;
  const [s,a,o,r]=await Promise.all(['/api/snapshots','/api/acquisitions','/api/observations','/api/reconstructions'].map(get));
  if(serial!==listSerial)return;
  snapshots=s.snapshots;acquisitions=a.acquisitions;observations=o.observations;runs=r.reconstructions;
  const oldSource=$('measure-snapshot').value;
  fillOptions('measure-snapshot',snapshots,'snapshot_id',s=>`${s.resolution} × ${s.resolution} · ${s.snapshot_id.slice(0,8)}`);
  fillOptions('acquisitions',acquisitions,'acquisition_id',a=>`${a.acquisition_id.slice(0,8)} · source ${a.snapshot_id.slice(0,8)} · ${a.shape.join(' × ')}`);
  for(const id of ['measure-observation','reconstruct-observation'])fillOptions(id,observations,'observation_id',o=>`${o.observation_id.slice(0,8)} · σ ${o.noise.sigma} · seed ${o.noise.seed}`);
  fillOptions('reconstructions',runs,'reconstruction_id',r=>`${r.reconstruction_id.slice(0,8)} · ${r.status} · K ${r.completed_iterations}/${r.requested_iterations}`);
  if(oldSource&&!$('measure-snapshot').value)selectSource('');
  availability();window.dispatchEvent(new CustomEvent('ct-records',{detail:{observations,runs}}));
}
function stage(name,status){const prefix=(name.startsWith('exact_')||name.startsWith('randomized_'))?'uq':name==='reconstruction'?'reconstruct':'measure';$(prefix+'-state').textContent=$(prefix+'-nav-state').textContent=status;}
async function watch(job,done){
  const id=job.job_id;
  if(polled.has(id))return;polled.add(id);
  $('job-panel').hidden=false;
  const row=document.createElement('div'),label=document.createElement('span'),cancel=document.createElement('button');row.className='job-row';row.dataset.jobId=id;
  cancel.textContent='Cancel';cancel.onclick=async()=>{try{await api(`/api/jobs/${id}/cancel`,{});cancel.disabled=true;label.textContent+=' · Cancellation requested';}catch(e){message(e.message);}};
  row.append(label,cancel);$('jobs').prepend(row);
  try{
    while(true){
      label.textContent=`${job.kind} ${id.slice(0,8)} · ${job.status} · ${job.stage} · ${job.completed}${job.total===null?'':` / ${job.total}`}${job.cancellation_requested?' · Cancellation requested':''}`;
      row.dataset.status=job.status;
      if(['completed','canceled','failed'].includes(job.status)){
        cancel.disabled=true;
        if(job.kind!=='replay')stage(job.kind,job.status);
        if(job.error)message(job.error.message);
        await refresh();await done?.(job);return job;
      }
      if(job.kind!=='replay')stage(job.kind,job.status);
      await new Promise(resolve=>setTimeout(resolve,150));job=await get('/api/jobs/'+id);
    }
  }catch(e){cancel.disabled=true;label.textContent+=' · Job unavailable';message(e.message);throw e;}
}
async function showObservation(id,{adoptForm=false}={}){
  const serial=++observationSerial;
  $('measure-status').textContent='Loading selected observation · Previous arrays hidden.';$('scan-results').hidden=true;
  if(!id){scan=null;availability();return;}
  try{
    const observation=await get('/api/observations/'+encodeURIComponent(id));
    const acquisition=await get('/api/acquisitions/'+observation.acquisition_id);
    const truth=await get('/api/snapshots/'+acquisition.snapshot_id);
    if(serial!==observationSerial)return;
    scan={observation,acquisition,truth};$('measure-observation').value=id;$('acquisitions').value=acquisition.acquisition_id;
    if(adoptForm){
      selectSource(acquisition.snapshot_id,false);
      const g=acquisition.geometry;$('n-angles').value=g.sinogram_shape[0];$('n-detectors').value=g.sinogram_shape[1];
      $('angle-start').value=g.range_degrees[0];$('angle-end').value=g.range_degrees[1];$('sigma').value=observation.noise.sigma;$('noise-seed').value=observation.noise.seed;
    }
    $('reconstruct-observation').value=id;reconstructGeneration++;await stepSummary();
    if(serial!==observationSerial)return;
    renderScan();availability();message('');
  }catch(e){if(serial===observationSerial){scan=null;$('scan-results').hidden=true;$('measure-status').textContent='Observation unavailable. Refresh records or generate a new scan.';availability();message(e.message);}}
}
function renderScan(){
  window.dispatchEvent(new CustomEvent('ct-observation-selected',{detail:scan.observation.observation_id}));
  const {acquisition:a,observation:o,truth}=scan,na=a.shape[0],nd=a.shape[1],range=sharedRange(a.y_clean,o.y);
  $('scan-results').hidden=false;paintRaster($('scan-truth'),Uint8Array.from(truth.pixels,x=>Math.round(255*x)),truth.resolution);
  drawSinogram($('clean-sinogram'),a.y_clean,na,nd,range);drawSinogram($('observed-sinogram'),o.y,na,nd,range);
  $('observed-title').textContent=o.noise.sigma===0?'Observed sinogram — no added noise':'Observed sinogram — Gaussian noise';
  $('sino-low').textContent=range[0].toPrecision(6);$('sino-high').textContent=range[1].toPrecision(6);
  document.querySelectorAll('.sinogram-axes').forEach(el=>el.textContent=`Horizontal: normal angle ${(a.geometry.angles[0]*180/Math.PI).toFixed(2)}° → ${(a.geometry.angles.at(-1)*180/Math.PI).toFixed(2)}° (actual samples). Vertical: offset ${a.geometry.offsets[0].toFixed(4)} bottom → ${a.geometry.offsets.at(-1).toFixed(4)} top.`);
  $('scan-source').textContent=`Snapshot ${a.snapshot_id} · ${a.resolution} × ${a.resolution}`;
  $('scan-summary').textContent=`${na} angles × ${nd} offsets = ${na*nd} measurements · ${a.operator.nnz} sparse weights · σ ${o.noise.sigma}, seed ${o.noise.seed} · operator ${a.operator_cache_hit?'reused':'built'} · clean measurements ${a.clean_cache_hit?'reused':'computed'} · ${a.elapsed_seconds.toFixed(3)} s acquisition`;
  const {y_clean,...am}=a,{y,...om}=o;$('scan-metadata').textContent=JSON.stringify({acquisition:am,observation:om},null,2);
  $('ray-angle').max=na-1;$('ray-detector').max=nd-1;$('ray-angle').value=0;$('ray-detector').value=0;rayInspect();scanMismatch();
}
function rayInspect(){
  if(!scan)return;const j=numeric('ray-angle'),ell=numeric('ray-detector'),{acquisition:a,observation:o}=scan;
  if(!Number.isInteger(j)||!Number.isInteger(ell)||j<0||ell<0||j>=a.shape[0]||ell>=a.shape[1])throw new Error('Ray index outside the displayed scan.');
  const mu=j*a.shape[1]+ell;
  $('ray-info').textContent=`Acquisition ${a.acquisition_id}\nObservation ${o.observation_id}\nAngle index ${j}: ${(a.geometry.angles[j]*180/Math.PI).toPrecision(12)}°\nDetector index ${ell}: offset ${a.geometry.offsets[ell].toPrecision(17)}\nMeasurement index μ = ${mu}\nClean: ${a.y_clean[mu].toPrecision(17)}\nObserved: ${o.y[mu].toPrecision(17)}\nDifference (simulated noise): ${(o.y[mu]-a.y_clean[mu]).toPrecision(17)}`;
}
let stepSerial=0;
async function stepSummary(){
  const serial=++stepSerial,id=$('reconstruct-observation').value;
  if(!id){$('step-summary').textContent='Select an observation.';availability();return;}
  try{
    const o=await get('/api/observations/'+id),a=await get('/api/acquisitions/'+o.acquisition_id);
    if(serial!==stepSerial)return;
    const L=a.operator.L_safe,manual=$('step-mode').value==='manual',omega=manual?Number($('manual-step').value):1/(2*L);
    $('step-summary').textContent=`Observation ${id.slice(0,8)} · frozen source ${a.snapshot_id.slice(0,8)} (${a.resolution} × ${a.resolution}) · σ ${o.noise.sigma}, seed ${o.noise.seed} · ‖A‖₁=${a.operator.norm_one.toPrecision(7)}, ‖A‖∞=${a.operator.norm_infinity.toPrecision(7)}, L_safe=${L.toPrecision(7)} · ω=${omega.toPrecision(9)}. ${omega<=0||omega>=2/L||!Number.isFinite(omega)?'Invalid step.':omega>1/L?'Stable under conservative bound; nonoscillatory condition not certified.':'Stable, nonoscillatory step certified.'}`;
    availability();
  }catch(e){if(serial===stepSerial){$('step-summary').textContent='Observation unavailable.';message(e.message);}}
}
function runMismatch(){if(run)$('reconstruct-status').textContent=`Displayed run ${run.reconstruction_id.slice(0,8)} · ${run.status} · ${run.observation_id===$('reconstruct-observation').value&&run.requested_iterations===Number($('iterations').value)&&run.configuration.step_policy===($('step-mode').value==='manual'?'manual_safe_bound_v1':'safe_induced_norm_v1')&&($('step-mode').value!=='manual'||run.configuration.omega===Number($('manual-step').value))?'Matches current form.':'Differs from current form; captured run is unchanged.'}`;}
async function showRun(id){
  const serial=++runSerial;++inspectSerial;clearTimeout(inspectionTimer);iteration=null;
  if(replayJob)api(`/api/jobs/${replayJob}/cancel`,{}).catch(()=>{});replayJob=null;
  $('reconstruction-results').hidden=true;$('reconstruct-status').textContent='Loading selected reconstruction…';
  if(!id){run=null;return;}
  try{
    const [r,h]=await Promise.all([get('/api/reconstructions/'+id),get('/api/reconstructions/'+id+'/history')]);
    const truth=await get('/api/snapshots/'+r.snapshot_id);
    if(serial!==runSerial)return;
    run=r;history=h;runTruth=truth;$('reconstructions').value=id;$('reconstruction-results').hidden=false;
    $('iteration-slider').max=$('inspect-iteration').max=r.completed_iterations;
    $('iteration-slider').value=$('inspect-iteration').value=r.completed_iterations;
    $('ct-row').max=$('ct-column').max=r.resolution-1;$('ct-row').value=$('ct-column').value=0;
    paintRaster($('reconstruction-truth'),Uint8Array.from(truth.pixels,x=>Math.round(255*x)),truth.resolution);
    $('run-summary').textContent=`Run ${id} · ${r.status} · ${r.completed_iterations}/${r.requested_iterations} completed updates · ${r.stop_reason} · ${r.elapsed_seconds.toFixed(3)} s · observation ${r.observation_id} · source ${r.snapshot_id}`;
    const {pixels,...metadata}=r;$('run-metadata').textContent=JSON.stringify(metadata,null,2);
    runMismatch();await inspectIteration(r.completed_iterations);message('');
  }catch(e){if(serial===runSerial){run=null;$('reconstruction-results').hidden=true;$('reconstruct-status').textContent='Reconstruction unavailable. Refresh records.';message(e.message);}}
}
function drawIteration(cp){
  iteration=cp;window.dispatchEvent(new CustomEvent('ct-inspected',{detail:{reconstruction_id:run.reconstruction_id,iteration:cp.iteration}}));
  const n=run.resolution;
  paintRaster($('reconstruction-image'),Uint8Array.from(cp.pixels,x=>Math.round(255*Math.min(1,Math.max(0,x)))),n);
  let min=Infinity,max=-Infinity;for(const x of cp.pixels){min=Math.min(min,x);max=Math.max(max,x);}
  $('raw-range').textContent=`Raw min ${min.toPrecision(8)} · max ${max.toPrecision(8)}${min<0||max>1?' · Display clips values outside [0,1]; raw data retained.':''}`;
  $('iteration-status').textContent=`Inspecting iteration ${cp.iteration} (${cp.origin}) · Run remains ${run.status} at ${run.completed_iterations} completed updates.`;
  $('iteration-status').dataset.iteration=cp.iteration;
  const h=history.history[cp.iteration],d=history.diagnostics.history[cp.iteration];
  $('iteration-metrics').textContent=`Iteration ${cp.iteration}\nResidual norm ${h.residual_norm.toPrecision(12)} · objective ${h.objective.toPrecision(12)}\nRelative residual ${h.relative_residual===null?'undefined (zero observation norm)':h.relative_residual.toPrecision(12)}\nValidation only: RMSE ${d.rmse.toPrecision(12)} · relative image error ${d.relative_error===null?'undefined (all-black truth)':d.relative_error.toPrecision(12)}`;
  plot($('residual-chart'),history.history.map(h=>h.residual_norm),cp.iteration,'Measurement residual norm');
  plot($('error-chart'),history.diagnostics.history.map(h=>h.rmse),cp.iteration,'Image RMSE — validation only');pixelInspect();
}
async function inspectIteration(k){
  if(!run)return;
  if(!Number.isInteger(k)||k<0||k>run.completed_iterations)throw new Error('Iteration is outside the completed run.');
  const serial=++inspectSerial,id=run.reconstruction_id,hash=run.configuration_hash;
  if(replayJob)api(`/api/jobs/${replayJob}/cancel`,{}).catch(()=>{});replayJob=null;
  $('iteration-slider').value=$('inspect-iteration').value=k;
  $('iteration-status').textContent=`Loading iteration ${k} · Previous image belongs to iteration ${iteration?.iteration??'unavailable'}.`;
  const valid=()=>serial===inspectSerial&&run?.reconstruction_id===id&&run?.configuration_hash===hash;
  try{
    let cp;
    try{cp=await get(`/api/reconstructions/${id}/checkpoints/${k}`);}
    catch(e){
      if(e.code!=='checkpoint_missing')throw e;
      if(!valid())return;
      const reply=await api(`/api/reconstructions/${id}/checkpoint-requests`,{request_id:requestId(),iteration:k});
      if(reply.status===200)cp=reply.result;
      else{
        const job=reply.result;
        if(!valid()){await api(`/api/jobs/${job.job_id}/cancel`,{});watch(job).catch(()=>{});return;}
        replayJob=job.job_id;$('iteration-status').textContent=`Replaying iteration ${k} · Previous image: iteration ${iteration?.iteration??'unavailable'}.`;
        const terminal=await watch(job);
        if(!valid())return;replayJob=null;
        if(terminal.status!=='completed')throw new Error(`Iteration replay ${terminal.status}. The original run is unchanged.`);
        cp=await get(`/api/reconstructions/${id}/checkpoints/${k}`);
      }
    }
    if(!valid())return;
    if(cp.reconstruction_id!==id||cp.configuration_hash!==hash||cp.iteration!==k)throw new Error('Checkpoint identity mismatch.');
    drawIteration(cp);
  }catch(e){if(valid()){$('iteration-status').textContent=`Iteration ${k} unavailable · Previous image: ${iteration?.iteration??'none'}.`;message(e.message);}}
}
function pixelInspect(){
  if(!run||!iteration)return;const row=numeric('ct-row'),col=numeric('ct-column'),n=run.resolution;
  if(!Number.isInteger(row)||!Number.isInteger(col)||row<0||col<0||row>=n||col>=n)throw new Error('Pixel outside the displayed reconstruction.');
  const index=row*n+col,x=iteration.pixels[index],truth=runTruth.pixels[index];
  $('reconstruction-pixel').textContent=`Run ${run.reconstruction_id} · iteration ${iteration.iteration}\nRow ${row}, column ${col}, index ${index}\nSpatial center (${(-1+(col+.5)*2/n).toPrecision(8)}, ${(1-(row+.5)*2/n).toPrecision(8)})\nRaw reconstruction ${x.toPrecision(17)}\nValidation only: truth ${truth.toPrecision(17)} · difference ${(x-truth).toPrecision(17)}`;
  for(const id of ['truth-marker','reconstruction-marker']){const ctx=$(id).getContext('2d'),size=512/n;ctx.clearRect(0,0,512,512);ctx.strokeStyle='#f0a14b';ctx.lineWidth=2;ctx.strokeRect(col*size+1,row*size+1,size-2,size-2);}
}
function safe(handler){return async e=>{try{await handler(e);}catch(error){message(error.message);}};}
document.querySelectorAll('[data-workspace]').forEach(button=>button.onclick=()=>{
  if(document.querySelector('#paint-controls').disabled)return;
  for(const name of ['draw','measure','reconstruct','uq'])$(name+'-workspace').hidden=name!==button.dataset.workspace;
  document.querySelectorAll('[data-workspace]').forEach(b=>{if(b===button)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});
});
window.addEventListener('ct-draft',e=>{draftState=e.detail;scanMismatch();});
window.addEventListener('ct-snapshots',safe(async e=>{await refresh();if(e.detail.selectedId)selectSource(e.detail.selectedId);}));
window.addEventListener('ct-snapshot-selected',e=>selectSource(e.detail));
$('measure-snapshot').onchange=()=>selectSource($('measure-snapshot').value);
$('ct-refresh').onclick=safe(async()=>{await refresh();if(scan&&!observations.some(o=>o.observation_id===scan.observation.observation_id)){scan=null;$('scan-results').hidden=true;$('measure-status').textContent='Observation unavailable after backend restart.';}if(run&&!runs.some(r=>r.reconstruction_id===run.reconstruction_id)){run=null;iteration=null;++inspectSerial;$('reconstruction-results').hidden=true;$('reconstruct-status').textContent='Reconstruction unavailable after backend restart.';}availability();});
$('acquisition-form').addEventListener('input',()=>{measureGeneration++;scanMismatch();});
$('acquisition-form').onsubmit=safe(async e=>{
  e.preventDefault();const generation=++measureGeneration,selection=observationSerial;
  const body={request_id:requestId(),snapshot_id:$('measure-snapshot').value,geometry:currentGeometry(),noise:currentNoise()};
  const {result:job}=await api('/api/acquisitions',body);message('');
  watch(job,async done=>{if(done.status==='completed'&&generation===measureGeneration&&selection===observationSerial)await showObservation(done.result.observation_id);}).catch(()=>{});
});
async function newObservation(random){
  if(!scan)throw new Error('Select an existing acquisition first.');
  const aid=scan.acquisition.acquisition_id,generation=++measureGeneration,selection=observationSerial;
  const noise=random?{sigma:numeric('sigma'),new_seed:true}:currentNoise();
  const {result:job}=await api(`/api/acquisitions/${aid}/observations`,{request_id:requestId(),noise});
  // Seed is chosen at submission and shown explicitly, never inferred from a UUID.
  if(generation===measureGeneration)$('noise-seed').value=job.inputs.noise.seed;
  watch(job,async done=>{if(done.status==='completed'&&generation===measureGeneration&&selection===observationSerial)await showObservation(done.result.observation_id);}).catch(()=>{});
}
$('new-observation').onclick=safe(()=>newObservation(false));$('new-noise').onclick=safe(()=>newObservation(true));
$('measure-observation').onchange=safe(async()=>{measureGeneration++;await showObservation($('measure-observation').value,{adoptForm:true});});
$('acquisitions').onchange=safe(async()=>{measureGeneration++;const list=observations.filter(o=>o.acquisition_id===$('acquisitions').value);await showObservation(list.at(-1)?.observation_id||'',{adoptForm:true});});
$('inspect-ray').onclick=safe(rayInspect);
for(const id of ['clean-sinogram','observed-sinogram'])$(id).onclick=safe(e=>{
  if(!scan)return;const rect=$(id).getBoundingClientRect(),[na,nd]=scan.acquisition.shape;
  $('ray-angle').value=Math.min(na-1,Math.max(0,Math.floor((e.clientX-rect.left)*na/rect.width)));
  $('ray-detector').value=nd-1-Math.min(nd-1,Math.max(0,Math.floor((e.clientY-rect.top)*nd/rect.height)));rayInspect();
});
$('reconstruct-observation').onchange=safe(async()=>{reconstructGeneration++;await stepSummary();runMismatch();});
$('reconstruction-form').addEventListener('input',()=>{reconstructGeneration++;runMismatch();});
$('step-mode').onchange=safe(async()=>{$('manual-step').disabled=$('step-mode').value!=='manual';$('manual-step').required=!$('manual-step').disabled;await stepSummary();runMismatch();});
$('manual-step').onchange=safe(stepSummary);
$('reconstruction-form').onsubmit=safe(async e=>{
  e.preventDefault();const generation=++reconstructGeneration,selection=runSerial;
  const step=$('step-mode').value==='manual'?{mode:'manual',value:numeric('manual-step')}:{mode:'automatic_safe'};
  const {result:job}=await api('/api/reconstructions',{request_id:requestId(),observation_id:$('reconstruct-observation').value,iterations:numeric('iterations'),step});
  message('');watch(job,async done=>{if(done.result?.reconstruction_id&&generation===reconstructGeneration&&selection===runSerial)await showRun(done.result.reconstruction_id);}).catch(()=>{});
});
$('reconstructions').onchange=safe(()=>showRun($('reconstructions').value));
$('show-iteration').onclick=safe(()=>inspectIteration(numeric('inspect-iteration')));
$('inspect-iteration').onchange=safe(()=>inspectIteration(numeric('inspect-iteration')));
$('iteration-slider').oninput=()=>{
  const k=Number($('iteration-slider').value);$('inspect-iteration').value=k;++inspectSerial;
  $('iteration-status').textContent=`Requested iteration ${k} · Previous image: ${iteration?.iteration??'none'}.`;
  clearTimeout(inspectionTimer);inspectionTimer=setTimeout(()=>inspectIteration(k).catch(e=>message(e.message)),150);
};
$('inspect-reconstruction-pixel').onclick=safe(pixelInspect);
for(const id of ['truth-marker','reconstruction-marker'])$(id).onclick=safe(e=>{
  if(!run)return;const r=$(id).getBoundingClientRect(),n=run.resolution;
  $('ct-row').value=Math.min(n-1,Math.max(0,Math.floor((e.clientY-r.top)*n/r.height)));$('ct-column').value=Math.min(n-1,Math.max(0,Math.floor((e.clientX-r.left)*n/r.width)));pixelInspect();
});
refresh().then(async()=>{
  const jobs=await get('/api/jobs');for(const job of jobs.jobs)if(['running','queued'].includes(job.status))watch(job).catch(()=>{});
}).catch(e=>message(e.message));

export {api,get,watch,refresh};
