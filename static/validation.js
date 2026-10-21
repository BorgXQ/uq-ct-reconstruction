import {clearCoverageDiagnostics,renderCoverageDiagnostics} from './coverage-diagnostics.js';
import {coverageBytes,viridisGradient} from './coverage-colors.js';
import {api,get,watch} from './ct.js';
const $=id=>document.getElementById(id);
let selected=null,revision=0;
const running=new Map();
$('coverage-gradient').style.background=viridisGradient;
export function clearCoverage(){clearCoverageDiagnostics();selected=null;revision++;$('coverage-image').hidden=true;$('coverage-scale').hidden=true;$('coverage-title').textContent='Empirical coverage';$('coverage-status').hidden=false;$('coverage-status').textContent='Awaiting coverage validation.';}
function status(a,text){if(selected===a.analysis_id){$('coverage-status').hidden=false;$('coverage-status').textContent=text;}}
function render(study,analysis){
  renderCoverageDiagnostics(study,analysis);
  const n=study.plan.geometry.resolution,values=study.maps.corrected?.coverage;
  const summary=study.summaries.corrected,finite=values?.filter(Number.isFinite)??[];
  const mean=finite.length?finite.reduce((sum,v)=>sum+v,0)/finite.length:null;
  $('coverage-status').textContent=`${study.status} · ${study.completed_trials}/${study.plan.trials} trials · fixed K = ${study.plan.iterations}.`;
  $('coverage-image').hidden=!values;$('coverage-scale').hidden=!values;
  if(values){
    const bytes=coverageBytes(values);
    const small=document.createElement('canvas');small.width=small.height=n;small.getContext('2d').putImageData(new ImageData(bytes,n,n),0,0);
    const ctx=$('coverage-image').getContext('2d');ctx.imageSmoothingEnabled=false;ctx.drawImage(small,0,0,512,512);

  }
  $('coverage-status').hidden=study.status==='completed'&&!!values;
  $('coverage-title').textContent=`Empirical coverage (mean = ${mean===null?'unavailable':(100*mean).toFixed(2)+'%'}, ${summary.returned_intervals} trials)`;
}
export async function restoreCoverage(a){
  selected=a.analysis_id;const token=++revision;
  if(!a.center||!a.intervals_available){status(a,'Coverage unavailable: no selected reconstruction with confidence intervals.');return;}
  if(running.has(a.analysis_id)){status(a,'Coverage validation running; progress and cancellation are available in Numerical jobs.');return;}
  const records=await get('/api/validation-studies');if(token!==revision)return;
  const match=records.studies.filter(s=>s.plan.template_id===a.analysis_id&&s.plan.protocol==='fixed_algorithm'&&s.plan.comparison==='common_k'&&s.plan.iterations===a.iteration&&s.plan.methods.includes('corrected')).at(-1);
  if(!match){status(a,'No coverage study saved for this reconstruction. Run reconstruction to calculate coverage.');return;}
  const study=await get('/api/validation-studies/'+match.study_id+'?arrays=1');if(token===revision)render(study,a);
}
export async function runCoverage(a,trials){
  if(!a.center||!a.intervals_available){status(a,'Coverage unavailable: no selected reconstruction with confidence intervals.');return;}
  running.set(a.analysis_id,true);status(a,`Calculating coverage across ${trials} trials at fixed K = ${a.iteration}. Progress and cancellation are available in Numerical jobs.`);
  try{
    const {result:job}=await api('/api/validation-studies',{request_id:crypto.randomUUID(),template_id:a.analysis_id,template_kind:'randomized',protocol:'fixed_algorithm',comparison:'common_k',trials,iterations:a.iteration,root_seed:crypto.getRandomValues(new Uint32Array(1))[0],methods:['corrected'],scope:'intervals',purpose:'exploratory_study',pixels:[],representatives:[],diagnostics:'none',diagnostic_iterations:[],reference:false});
    const done=await watch(job);
    if(done.result?.study_id){const study=await get('/api/validation-studies/'+done.result.study_id+'?arrays=1');if(selected===a.analysis_id)render(study,a);}
    else status(a,`Coverage validation ${done.status}: ${done.error?.message||'no completed coverage result'}. Reconstruction remains available.`);
  }catch(e){status(a,'Coverage validation failed: '+e.message+'. Reconstruction remains available.');}
  finally{running.delete(a.analysis_id);}
}
