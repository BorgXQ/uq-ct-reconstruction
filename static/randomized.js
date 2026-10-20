import {rowPlot} from './validation-presentation.js';
import {api,get,watch} from './ct.js';
import {limits,mapBytes,stoppingPlot} from './exact-view.js';
const $=id=>document.getElementById(id),num=id=>Number($(id).value),uid=()=>crypto.randomUUID();
const fmt=x=>Number.isFinite(x)?x.toPrecision(17):'unavailable';
let rankResolution=null;
let observations=[],truth=null,pendingObservation=null,base=null,view=null,diagnostic=null,history=[],generation=0,serial=0,inspectSerial=0,diagSerial=0,signature=null,inspectionJob=null,ownComparison=null;
const guarded=fn=>async(...args)=>{try{await fn(...args);}catch(e){$('rq-error').textContent=e.message;}};
const formSignature=()=>JSON.stringify([...$('rq-form').querySelectorAll('input,select')].map(e=>[e.id,e.type==='checkbox'?e.checked:e.value]));
function options(id,rows,key,label){const el=$(id),old=el.value;el.replaceChildren(new Option('Select a record',''));for(const r of rows)el.add(new Option(label(r),r[key]));if(rows.some(r=>r[key]===old))el.value=old;}
function controls(){
  const automatic=$('rq-mode').value==='automatic';
  $('rq-k-label').hidden=automatic;$('rq-budget-label').hidden=!automatic;
  $('rq-k').disabled=automatic;$('rq-k').required=!automatic;
  $('rq-budget').disabled=!automatic;$('rq-budget').required=automatic;
  const o=observations.find(o=>o.observation_id===$('rq-observation').value);
  if(o&&o.resolution!==rankResolution){$('rq-rank').value=({16:32,32:64,64:128})[o.resolution]??Math.min(128,o.resolution*2);rankResolution=o.resolution;}
  $('rq-context').textContent=o?`${o.resolution} × ${o.resolution}; observation ${o.observation_id.slice(0,8)}; noise SD ${o.noise.sigma}.`:'Select an observation from Measure.';
  $('rq-submit').disabled=!o;$('rq-submit').textContent='Run reconstruction';
  if(base)$('rq-mismatch').textContent=signature===formSignature()?'Displayed result matches submitted controls.':'Displayed result keeps its captured settings. Recalculate to apply current controls.';
}
async function lists(){
  const r=await get('/api/randomized-analyses');
  options('rq-analyses',r.randomized_analyses,'analysis_id',a=>`${a.analysis_id.slice(0,8)} · ${a.status} · K ${a.selected_k??'none'} · r ${a.configuration.rank}`);controls();
}
window.addEventListener('ct-records',e=>{observations=e.detail.observations;options('rq-observation',observations,'observation_id',o=>`${o.observation_id.slice(0,8)} · ${o.resolution}² · σ ${o.noise.sigma}`);if(pendingObservation&&observations.some(o=>o.observation_id===pendingObservation))$('rq-observation').value=pendingObservation;lists().catch(e=>$('rq-error').textContent=e.message);});
window.addEventListener('ct-observation-selected',e=>{pendingObservation=e.detail;if($('rq-observation').value!==e.detail){$('rq-observation').value=e.detail;generation++;}controls();});
$('rq-observation').onchange=()=>{pendingObservation=$('rq-observation').value;};
$('rq-form').addEventListener('input',()=>{generation++;controls();});$('rq-form').addEventListener('change',()=>{generation++;controls();});
function request(){
  const body={request_id:uid(),rank:num('rq-rank'),oversampling:num('rq-p'),sketch_seed:num('rq-sketch'),probe_seed:num('rq-probe'),probe_count:num('rq-count'),block_size:num('rq-block'),epsilon:num('rq-epsilon'),delta:num('rq-delta'),backend:'matrix_free',reference:true,
    eta:$('rq-eta').value.trim()===''?null:{value:num('rq-eta'),provenance:'User-specified omitted-bias assumption; not verified.'}};
  return ['/api/randomized-analyses',{...body,observation_id:$('rq-observation').value,step_policy:'safe_induced_norm_v1',alpha:num('rq-alpha'),settings_origin:'unverified',declaration:'User-specified settings; noise independence has not been established.',
    source:{M:num('rq-M'),nu:num('rq-nu'),provenance:'User-specified source assumption; not verified against the drawing.'},
    selection:$('rq-mode').value==='automatic'?{mode:'automatic',budget:num('rq-budget')}:{mode:'fixed',iterations:num('rq-k'),provenance:'imported_selection_unknown'}}];
}
$('rq-form').onsubmit=guarded(async e=>{
  e.preventDefault();const [url,body]=request(),g=++generation,s=serial,sig=formSignature();$('rq-error').textContent='';
  const {result:job}=await api(url,body);$('rq-status').textContent=`Captured job ${job.job_id}; use the shared job panel to cancel.`;
  const done=await watch(job);await lists();
  if(g===generation&&s===serial&&done.result?.analysis_id)await show(done.result.analysis_id,sig);
  else if(done.status==='failed')throw Error(done.error?.message||'Analysis failed. Previous records are preserved.');
});
async function show(aid,sig=null){
  const s=++serial;++inspectSerial;++diagSerial;diagnostic=null;truth=null;ownComparison=null;base=view=null;$('rq-results').hidden=true;
  if(!aid)return;
  if(inspectionJob)api(`/api/jobs/${inspectionJob}/cancel`,{}).catch(()=>{});
  try{
    const [a,h]=await Promise.all([get('/api/randomized-analyses/'+aid),get(`/api/randomized-analyses/${aid}/stopping-history`)]);
    if(s!==serial)return;
    const snapshot=await get('/api/snapshots/'+a.snapshot_id);if(s!==serial)return;truth=snapshot.pixels;base=view=a;history=h.history;signature=sig;$('rq-analyses').value=aid;$('rq-results').hidden=false;
    $('rq-inspect-k').value=a.iteration??0;$('rq-inspect-k').max=a.selected_k??0;
    $('rq-main-row').value=Math.floor(a.shape[0]/2);$('rq-main-row').max=a.shape[0]-1;
    $('rq-row').value=$('rq-col').value=0;$('rq-row').max=$('rq-col').max=a.shape[0]-1;
    $('rq-own-info').textContent=$('rq-own-summary').textContent=$('rq-own-pixel').textContent='';$('rq-own-maps').replaceChildren();render();controls();
    if(a.status==='completed')window.dispatchEvent(new CustomEvent('randomized-selected',{detail:{analysis_id:aid,iteration:a.iteration}}));
    const ds=await get('/api/approximation-diagnostics');if(s!==serial)return;
    const d=ds.approximation_diagnostics.filter(d=>d.analysis_id===aid&&!d.view_id).at(-1);
    if(d){const full=await get('/api/approximation-diagnostics/'+d.diagnostic_id);if(s===serial){diagnostic=full;render();}}
  }catch(e){if(s===serial){$('rq-status').textContent='Analysis unavailable in this backend process.';$('rq-results').hidden=true;}throw e;}
}
function values(key){if(['actual_bias','absolute_error','inclusion'].includes(key))return diagnostic?.iteration===view?.iteration?diagnostic[key]:null;return view?.[key];}
function mapCard(parent,key,label,range=null,source=null){
  const card=document.createElement('article');card.className='rq-card';const title=document.createElement('h4');title.textContent=label;card.append(title);parent.append(card);
  const data=source||values(key),legend=document.createElement('p');legend.className='hint';card.append(legend);
  if(!data){legend.textContent='Unavailable: '+(key.startsWith('full')?view?.reference.reason||'No matching full reference.':key.includes('bias')||key==='inclusion'?'Request matching truth diagnostics.':view?.intervals_unavailable_reason||'No matching numerical array.');return;}
  const signed=['signed_residual','exact_residual','residual_estimation_error','correction_minus_full','actual_bias'].includes(key);
  range=range||limits(data);if(signed){const m=Math.max(...range.map(Math.abs));range=[-m,m];}
  const wrap=document.createElement('div');wrap.className='rq-image';const canvas=document.createElement('canvas'),marker=document.createElement('canvas');
  const n=Math.sqrt(data.length);canvas.width=canvas.height=n;marker.width=marker.height=512;marker.className='rq-marker';marker.dataset.n=n;
  canvas.getContext('2d').putImageData(new ImageData(mapBytes(data,range,{signed}),n,n),0,0);
  marker.tabIndex=0;marker.setAttribute('aria-label',label+'; click to select a pixel or use row and column controls');
  marker.onclick=e=>{const box=marker.getBoundingClientRect();$('rq-row').value=Math.min(n-1,Math.floor((e.clientY-box.top)*n/box.height));$('rq-col').value=Math.min(n-1,Math.floor((e.clientX-box.left)*n/box.width));pixel();};
  marker.onkeydown=e=>{const keys={ArrowUp:[-1,0],ArrowDown:[1,0],ArrowLeft:[0,-1],ArrowRight:[0,1]};if(keys[e.key]){e.preventDefault();$('rq-row').value=Math.max(0,Math.min(n-1,num('rq-row')+keys[e.key][0]));$('rq-col').value=Math.max(0,Math.min(n-1,num('rq-col')+keys[e.key][1]));pixel();}};
  wrap.append(canvas,marker);card.insertBefore(wrap,legend);
  const scale=document.createElement('div');scale.className='scale';const low=document.createElement('span'),bar=document.createElement('div'),high=document.createElement('span');low.textContent=fmt(range[0]);high.textContent=fmt(range[1]);if(signed)bar.style.background='linear-gradient(to right,#00f,#80c880,#f00)';scale.append(low,bar,high);card.insertBefore(scale,legend);
  legend.textContent=`Scale ${fmt(range[0])} → ${fmt(range[1])}${range[0]===range[1]?' · constant map':''}${key==='center'?' · display saturates outside [0,1]; raw values preserved':''}`;
}
function spectrum(){
  const svg=$('rq-spectrum'),exact=view.exact_eigenvalues||[],approx=view.approximate_eigenvalues||[];svg.replaceChildren();if(!approx.length)return;
  const ns='http://www.w3.org/2000/svg',pad=38,high=Math.max(...exact,...approx),end=Math.max(exact.length,approx.length);
  const x=i=>pad+(600-2*pad)*i/Math.max(1,end-1),y=v=>202-164*v/high;
  const node=(tag,attrs,text)=>{const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;svg.append(e);};
  node('path',{d:'M38 38V202H562',stroke:'#77887f',fill:'none'});
  for(const [a,color] of [[exact,'#28776a'],[approx,'#be7026']])if(a.length)node('polyline',{points:a.map((v,i)=>`${x(i)},${y(v)}`).join(' '),stroke:color,fill:'none','stroke-width':2});
  node('path',{d:`M${x(view.configuration.rank-1)} 38V202`,stroke:'#303d37','stroke-dasharray':'4 3'});
  node('text',{x:40,y:18,'font-size':12},'λ = s² · green full reference; orange approximate');node('text',{x:2,y:38,'font-size':10},high.toPrecision(3));
  node('text',{x:38,y:230,'font-size':11},`index 1 → ${end}; boundary r = ${view.configuration.rank}${!exact.length?' · full spectrum unavailable':''}`);
}
function render(){
  if(!base)return;
  renderPrimary();
  $('rq-status').textContent=`Saved analysis ${base.analysis_id} · ${base.status}`;
  $('rq-summary').textContent=`Observation ${base.observation_id} · full Landweber ${base.reconstruction_id??'not selected'} · operator ${base.operator_id} · selected K ${base.selected_k??'none'} · ω ${fmt(base.step.omega)} (${base.step.step_policy}) · σ ${base.configuration.sigma}. Requested/effective rank ${base.configuration.rank}/${base.factors.effective_rank}; sampled basis ${base.factors.basis_dimension}/${base.factors.sample_width}.`;
  $('rq-inspection-status').textContent=`Selected K ${base.selected_k??'none'}; inspecting k ${view.iteration??'none'}; ${view.selection_status}.`;
  $('rq-inspect').disabled=$('rq-diagnose').disabled=!view.center;
  $('rq-interpretation').textContent=`${view.coverage_interpretation||base.status}. ${view.intervals_available?'Raw intervals include Gaussian and declared bias allowances.':'Total-error interval unavailable: '+(view.intervals_unavailable_reason||base.status)+'.'} Nominal Gaussian level ${fmt(view.nominal_gaussian_level)}; reference factor product ${fmt(view.reference_randomized_coverage_expression)} — conditions not established. Marginal, not simultaneous coverage.`;
  $('rq-reference-status').textContent=view.reference.status==='available'?'Full reference available for this same step and k. Exact retained + tail recover full covariance.':'Full reference unavailable: '+view.reference.reason;
  $('rq-map-grid').replaceChildren();$('rq-extra').replaceChildren();
  if(view.center){
    const max=Math.max(...(view.full_pixel_sd||[]),...view.retained_sd,...view.pixel_sd);const range=[0,max];
    mapCard($('rq-map-grid'),'center','Full-operator Landweber density',[0,1]);mapCard($('rq-map-grid'),'full_pixel_sd','Full-spectrum pixel SD',range);
    mapCard($('rq-map-grid'),'retained_sd','Retained pixel SD — omitted covariance excluded',range);mapCard($('rq-map-grid'),'pixel_sd','Corrected noise scale — target unverified',range);
    mapCard($('rq-extra'),$('rq-map-choice').value,$('rq-map-choice').selectedOptions[0].textContent);pixel();
  }else $('rq-pixel-info').textContent='No selected center or interval. The computation budget is not a stopping result.';
  $('rq-bias').textContent=`Full ${view.full_bias_policy||'reference'} allowance: ${fmt(view.full_bias_allowance?.value)}. Exact retained: ${fmt(view.tsvd?.retained_bias?.value)}; exact tail: ${fmt(view.tsvd?.tail_bias?.value)}; combined split: ${fmt(view.tsvd?.combined_bias)}. Randomized retained approximation: ${fmt(view.retained_bias_allowance?.value)}; declared η ${fmt(view.configuration.eta?.value)}; inflated total: ${fmt(view.total_bias_allowance?.value)}. Both randomized bias inequalities need justification.`;
  $('rq-stopping-label').textContent=`${base.selection.status}; ${history.length} candidates evaluated; selected K ${base.selected_k??'none'}. ${base.status==='criterion_not_met'?'No crossing; no automatic cap reconstruction.':''}`;
  stoppingPlot($('rq-stopping'),history,base.selected_k);$('rq-stopping').hidden=!history.length;spectrum();
  $('rq-conditions').textContent=JSON.stringify({source:view.source_condition_status,retained_bias:view.retained_bias_assumption_status,omitted_bias:view.omitted_bias_assumption_status,unmet_conditions:view.unmet_conditions,probe_control:view.probe_control,coverage_factors:view.reference_coverage_factors,coverage_expression_applicable:view.coverage_expression_applicable,appendix:view.appendix,residual_reference_check:view.residual_reference_check},null,2);
  $('rq-metadata').textContent=JSON.stringify({configuration:base.configuration,configuration_hash:base.configuration_hash,factors:base.factors,probe_policy:base.probe_policy,probe_result:view.probe_result,full_spectrum:view.full_spectrum,reference:view.reference,cost:base.cost,timings:base.timings,bundle_seconds:view.bundle_seconds},null,2);
  $('rq-diagnostic-status').textContent=diagnostic?.iteration===view.iteration?diagnostic.effective_interpretation:'Not requested for this inspected iteration.';
  $('rq-diagnostic-info').textContent=diagnostic?.iteration===view.iteration?JSON.stringify(Object.fromEntries(Object.entries(diagnostic).filter(([,v])=>!Array.isArray(v))),null,2):'';
  if(diagnostic?.iteration===view.iteration)$('rq-interpretation').textContent+=' '+diagnostic.effective_interpretation;
}
function pixel(){
  if(!view?.center)return;const row=num('rq-row'),col=num('rq-col'),n=view.shape[0];if(!Number.isInteger(row)||!Number.isInteger(col)||row<0||col<0||row>=n||col>=n)return;
  const i=row*n+col;let text=`Analysis ${base.analysis_id}; exact ${base.configuration.exact_analysis_id??'unavailable'}\nObservation ${base.observation_id}; reconstruction ${base.reconstruction_id}\nFactor ${base.factors.factor_id}; probe set ${base.probe_policy.probe_id}\nRow ${row}, column ${col}, index ${i}; spatial center (${fmt(-1+(col+.5)*2/n)}, ${fmt(1-(row+.5)*2/n)})\nActual k ${view.iteration}; selected K ${base.selected_k}; ω ${fmt(base.step.omega)}; r ${base.factors.effective_rank}; p ${base.configuration.oversampling}; completed probes ${view.probe_result.completed_probes}\n`;
  for(const key of ['center','full_variance','full_pixel_sd','retained_variance','retained_sd','signed_residual','exact_residual','residual_estimation_error','residual_allowance','variance','pixel_sd','correction_minus_full','noise_half_width','total_half_width','lower','upper','full_lower','full_upper','tsvd_lower','tsvd_upper'])text+=`${key}: ${fmt(view[key]?.[i])}\n`;
  text+=`Retained bias ${fmt(view.retained_bias_allowance?.value)}; η ${fmt(view.configuration.eta?.value)}; inflated bias ${fmt(view.total_bias_allowance?.value)}; Gaussian quantile ${fmt(view.gaussian_quantile)}.\n${view.coverage_interpretation}; ${view.selection_status}.`;
  if(diagnostic?.iteration===view.iteration)text+=`\nValidation only: truth ${fmt(diagnostic.truth[i])}; actual bias ${fmt(diagnostic.actual_bias[i])}; error ${fmt(diagnostic.error[i])}; inclusion ${diagnostic.inclusion?.[i]??'unavailable'}.`;
  $('rq-pixel-info').textContent=text;
  if(ownComparison)$('rq-own-pixel').textContent=['exact','randomized'].map(name=>`${name} · K ${ownComparison.selections[name].selected_k??'unmet'} · pixel (${row}, ${col}) · density ${fmt(ownComparison[name+'_center']?.[i])} · interval [${fmt(ownComparison[name+'_lower']?.[i])}, ${fmt(ownComparison[name+'_upper']?.[i])}]`).join('\n');
  for(const canvas of document.querySelectorAll('.rq-marker')){const ctx=canvas.getContext('2d'),s=512/n;ctx.clearRect(0,0,512,512);ctx.strokeStyle='#ed912c';ctx.lineWidth=2;ctx.strokeRect(col*s+1,row*s+1,s-2,s-2);}
}
$('rq-inspect').onclick=guarded(async()=>{
  const aid=base.analysis_id,s=serial,t=++inspectSerial,k=num('rq-inspect-k');++diagSerial;
  if(inspectionJob)api(`/api/jobs/${inspectionJob}/cancel`,{}).catch(()=>{});
  const valid=()=>s===serial&&t===inspectSerial;
  $('rq-inspection-status').textContent=`Computing k ${k}; current maps remain at k ${view.iteration}.`;
  const {result:job}=await api(`/api/randomized-analyses/${aid}/inspection-requests`,{request_id:uid(),iteration:k});
  if(!valid())api(`/api/jobs/${job.job_id}/cancel`,{}).catch(()=>{});else inspectionJob=job.job_id;
  const done=await watch(job);if(!valid())return;inspectionJob=null;if(done.status!=='completed')throw Error('Inspection '+done.status);
  const v=await get('/api/randomized-views/'+done.result.view_id);if(!valid())return;
  if(v.analysis_id!==aid||v.configuration_hash!==base.configuration_hash||v.factors.factor_id!==base.factors.factor_id||v.iteration!==k)throw Error('Inspection identity mismatch.');
  view=v;diagnostic=null;render();
  if(k>0)window.dispatchEvent(new CustomEvent('randomized-selected',{detail:{analysis_id:aid,iteration:k}}));
});
$('rq-diagnose').onclick=guarded(async()=>{
  const aid=base.analysis_id,s=serial,t=++diagSerial,k=view.iteration,vid=view.view_id;
  const {result:job}=await api(`/api/randomized-analyses/${aid}/diagnostics`,{request_id:uid(),...(vid?{view_id:vid}:{})});const done=await watch(job);
  if(s!==serial||t!==diagSerial||k!==view.iteration)return;if(done.status!=='completed')throw Error('Diagnostics '+done.status);
  const d=await get('/api/approximation-diagnostics/'+done.result.diagnostic_id);if(s!==serial||t!==diagSerial||d.analysis_id!==aid||d.iteration!==view.iteration)return;
  diagnostic=d;$('rq-diagnostic-panel').open=true;render();
});
$('rq-own').onclick=guarded(async()=>{
  const s=serial,aid=base.analysis_id;const {result:job}=await api(`/api/randomized-analyses/${aid}/stopping-comparisons`,{request_id:uid(),budget:num('rq-own-budget'),c0:num('rq-own-c0'),bound_method:$('rq-own-bound').value});
  const done=await watch(job);if(s!==serial)return;if(done.status!=='completed')throw Error('Stopping comparison '+done.status);
  const c=await get('/api/stopping-comparisons/'+done.result.comparison_id);if(s!==serial)return;
  $('rq-own-summary').textContent=`${c.interpretation} Exact K ${c.selections.exact.selected_k??'unmet'} (${c.exact_bound_method}, c₀ ${c.exact_c0}); randomized K ${c.selections.randomized.selected_k??'unmet'} (balance 1). Shared ω ${fmt(c.step.omega)}.`;
  ownComparison=c;$('rq-own-maps').replaceChildren();for(const name of ['exact','randomized'])if(c[name+'_center'])mapCard($('rq-own-maps'),name+'_center',`${name} at K ${c.selections[name].selected_k}`,[0,1],c[name+'_center']);
  $('rq-own-info').textContent=JSON.stringify({comparison_id:c.comparison_id,step:c.step,selections:c.selections},null,2);pixel();
});
$('rq-analyses').onchange=guarded(()=>show($('rq-analyses').value));$('rq-refresh').onclick=guarded(lists);
$('rq-map-choice').onchange=()=>render();$('rq-pixel').onclick=pixel;$('rq-row').onchange=pixel;$('rq-col').onchange=pixel;

function renderPrimary(){
  const svg=$('rq-row-plot'),canvas=$('rq-main-image');canvas.hidden=!view?.center;canvas.parentElement.querySelector('.density-scale').hidden=!view?.center;
  $('rq-main-row').disabled=!view?.center;
  if(!view?.center){rowPlot(svg,null,null,0,0);$('rq-primary-context').textContent=`No reconstruction selected: ${base?.status??'unavailable'}.`;$('rq-primary-status').textContent='Automatic stopping did not find a crossing within the search budget. No cap reconstruction or interval is substituted.';return;}
  const n=view.shape[0],row=num('rq-main-row');if(!Number.isInteger(row)||row<0||row>=n)throw Error(`Choose a row from 0 to ${n-1}.`);
  const small=document.createElement('canvas');small.width=small.height=n;small.getContext('2d').putImageData(new ImageData(mapBytes(view.center,[0,1]),n,n),0,0);
  const ctx=canvas.getContext('2d');ctx.imageSmoothingEnabled=false;ctx.clearRect(0,0,512,512);ctx.drawImage(small,0,0,512,512);ctx.strokeStyle='#2878b8';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(0,(row+.5)*512/n);ctx.lineTo(512,(row+.5)*512/n);ctx.stroke();
  svg.style.height=canvas.getBoundingClientRect().height+'px';
  rowPlot(svg,truth,view,n,row,view.configuration.alpha);
  $('rq-primary-context').textContent=`Observation ${base.observation_id.slice(0,8)} · actual K = ${view.iteration} · selected K = ${base.selected_k} · rank ${base.configuration.rank} · noise SD ${base.configuration.sigma}. ${view.iteration!==base.selected_k?'Exploratory iteration inspection.':''}`;
  $('rq-primary-status').textContent=view.intervals_available?view.coverage_interpretation:`Confidence intervals unavailable: ${view.intervals_unavailable_reason||'declare justified source parameters and omitted-bias allowance'}.`;
}
$('rq-main-row').onchange=guarded(renderPrimary);
$('rq-main-image').onclick=guarded(e=>{if(!view?.center)return;const box=e.currentTarget.getBoundingClientRect();$('rq-main-row').value=Math.max(0,Math.min(view.shape[0]-1,Math.floor((e.clientY-box.top)*view.shape[0]/box.height)));renderPrimary();});
$('rq-selected').onclick=guarded(()=>show(base.analysis_id,signature));
controls();

// Keep the chart dimensions aligned with the square image after layout changes.
let primaryWidth=0;
new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(width>0&&width!==primaryWidth){primaryWidth=width;if(view?.center)renderPrimary();}}).observe(document.querySelector('.rq-primary-grid'));
