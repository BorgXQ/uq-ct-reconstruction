import {paintRaster} from './editor.js';
import {sharedRange,drawSinogram} from './ct-view.js';
const $=id=>document.getElementById(id);
let snapshots=[],acquisitions=[],observations=[],draftState=null;
let scan=null;
let measureGeneration=0,observationSerial=0,listSerial=0;
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
  $('nav-measure').disabled=!snapshots.length;$('nav-uq').disabled=!observations.length;
  $('uq-nav-state').textContent=observations.length?'ready':'not_ready';
  $('generate-scan').disabled=!$('measure-snapshot').value;
  $('new-observation').disabled=$('new-noise').disabled=!scan;
  if(!$('measure-snapshot').value)$('measure-nav-state').textContent=$('measure-state').textContent='not_ready';
  else if($('measure-nav-state').textContent==='not_ready')$('measure-nav-state').textContent=$('measure-state').textContent='ready';
}
function currentGeometry(){return {n_angles:numeric('n-angles'),n_detectors:numeric('n-detectors'),start_degrees:0,end_degrees:180};}
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
    if(resetDetector)$('n-detectors').value=2*(Math.ceil(Math.sqrt(2)*Math.floor(source.resolution/2))+1)+1;
    $('source-context').textContent=`Selected frozen image ${source.snapshot_id} · ${source.resolution} × ${source.resolution} · content ${source.content_hash}`;
  }else $('source-context').textContent='Select a frozen image. Historical images remain valid acquisition inputs.';
  availability();scanMismatch();
}
async function refresh(){
  const serial=++listSerial;
  const [s,a,o]=await Promise.all(['/api/snapshots','/api/acquisitions','/api/observations'].map(get));
  if(serial!==listSerial)return;
  snapshots=s.snapshots;acquisitions=a.acquisitions;observations=o.observations;
  const oldSource=$('measure-snapshot').value;
  fillOptions('measure-snapshot',snapshots,'snapshot_id',s=>`${s.resolution} × ${s.resolution} · ${s.snapshot_id.slice(0,8)}`);
  fillOptions('acquisitions',acquisitions,'acquisition_id',a=>`${a.acquisition_id.slice(0,8)} · source ${a.snapshot_id.slice(0,8)} · ${a.shape.join(' × ')}`);
  fillOptions('measure-observation',observations,'observation_id',o=>`${o.observation_id.slice(0,8)} · σ ${o.noise.sigma} · seed ${o.noise.seed}`);
  if(oldSource&&!$('measure-snapshot').value)selectSource('');
  availability();window.dispatchEvent(new CustomEvent('ct-records',{detail:{observations}}));
}
function stage(name,status){const prefix=['acquisition','observation'].includes(name)?'measure':'uq';$(prefix+'-state').textContent=$(prefix+'-nav-state').textContent=status;}
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
      $('sigma').value=observation.noise.sigma;$('noise-seed').value=observation.noise.seed;
    }
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
  document.querySelectorAll('.sinogram-axes').forEach(el=>el.textContent=`Horizontal: normal angle ${(a.geometry.angles[0]*180/Math.PI).toFixed(2)}° → ${(a.geometry.angles.at(-1)*180/Math.PI).toFixed(2)}° (actual samples). Vertical: offset ${a.geometry.offsets[0].toFixed(4)} bottom → ${a.geometry.offsets.at(-1).toFixed(4)} top (pixel units).`);
  $('scan-source').textContent=`Snapshot ${a.snapshot_id} · ${a.resolution} × ${a.resolution}`;
  $('scan-summary').textContent=`${na} angles × ${nd} offsets = ${na*nd} measurements · ${a.operator.nnz} sparse weights · σ ${o.noise.sigma}, seed ${o.noise.seed} · operator ${a.operator_cache_hit?'reused':'built'} · clean measurements ${a.clean_cache_hit?'reused':'computed'} · ${a.elapsed_seconds.toFixed(3)} s acquisition`;
  scanMismatch();
}
function safe(handler){return async e=>{try{await handler(e);}catch(error){message(error.message);}};}
document.querySelectorAll('[data-workspace]').forEach(button=>button.onclick=()=>{
  if(document.querySelector('#paint-controls').disabled)return;
  for(const name of ['draw','measure','uq'])$(name+'-workspace').hidden=name!==button.dataset.workspace;
  if(button.dataset.workspace==='uq'&&scan)window.dispatchEvent(new CustomEvent('ct-observation-selected',{detail:scan.observation.observation_id}));
  document.querySelectorAll('[data-workspace]').forEach(b=>{if(b===button)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});
});
window.addEventListener('ct-draft',e=>{draftState=e.detail;scanMismatch();});
window.addEventListener('ct-snapshots',safe(async e=>{await refresh();if(e.detail.selectedId)selectSource(e.detail.selectedId);}));
window.addEventListener('ct-snapshot-selected',e=>selectSource(e.detail));
$('measure-snapshot').onchange=()=>selectSource($('measure-snapshot').value);
$('ct-refresh').onclick=safe(async()=>{await refresh();if(scan&&!observations.some(o=>o.observation_id===scan.observation.observation_id)){scan=null;$('scan-results').hidden=true;$('measure-status').textContent='Observation unavailable after backend restart.';}availability();});
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
refresh().then(async()=>{
  const jobs=await get('/api/jobs');for(const job of jobs.jobs)if(['running','queued'].includes(job.status))watch(job).catch(()=>{});
}).catch(e=>message(e.message));

export {api,get,watch,refresh};
