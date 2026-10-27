const displayNumber=value=>value==null?'unavailable':String(Number(Number(value).toFixed(4)));
import {api,watch} from './ct.js';
const $=id=>document.getElementById(id);
let observation='',token=0,evidence=null,activeJob=null,notify=()=>{},message='Select an observation to choose the source bound.';
const cache=new Map();
const key=()=>`${$('rq-observation').value}:${$('rq-nu').value}`;
let acceptedKey='';
const compatible=()=>evidence?.status==='compatible'&&acceptedKey===key()&&Number.isFinite(evidence.M)&&evidence.M>=0;
function manual(){
  const field=$('rq-M'),filled=field.value.trim()!==''||field.validity?.badInput;
  const value=Number(field.value);
  return {filled,value,valid:filled&&!field.validity?.badInput&&Number.isFinite(value)&&value>=0};
}
export function sourceReady(){
  const nu=Number($('rq-nu').value),m=manual();
  return Number.isFinite(nu)&&nu>0&&(m.filled?m.valid:compatible());
}
export function calibratedSource(){
  if(!sourceReady())throw Error('Enter a nonnegative manual M, or calculate a compatible M from the drawing.');
  const m=manual();
  return m.filled
    ? {M:m.value,nu:Number($('rq-nu').value),provenance:'Manually declared source bound; source compatibility is not established by this entry.'}
    : {M:evidence.M,nu:evidence.nu,provenance:`Drawing ${evidence.snapshot_id}; full-normal spectral calibration; numerical evidence only.`};
}
function render(){
  const m=manual();
  $('rq-calibrated-M').value=compatible()?displayNumber(evidence.M):'';
  const selection=m.filled
    ? (m.valid?`Using manual M = ${displayNumber(m.value)}. This overrides calibration; it is a declared assumption, not a verified source bound.`:'Manual M must be a finite number ≥ 0. Clear it to use calibration.')
    : (compatible()?`Using calibrated M = ${displayNumber(evidence.M)}.`:'No usable M selected. Enter manual M or calculate it from the drawing.');
  $('rq-source-status').textContent=`${selection} ${message}`;
  notify();
}
function cancel(){
  ++token;
  if(activeJob){void api(`/api/jobs/${activeJob}/cancel`,{}).catch(()=>{});activeJob=null;}
}
function restore(){
  evidence=cache.get(key())??null;acceptedKey=evidence?key():'';
  message=evidence?`${evidence.reason}`:($('calibrate-source').checked?'M has not been calibrated for this observation and exponent.':'Automatic calibration is off.');
}
export function observationChanged(callback){
  notify=callback;
  const next=$('rq-observation').value;
  if(next!==observation){
    observation=next;cancel();restore();
    if(next&&$('calibrate-source').checked&&!evidence)void calculate();
    else render();
  }
}
async function calculate(){
  cancel();
  const serial=token,current=key(),oid=$('rq-observation').value,nu=Number($('rq-nu').value);
  if(!oid){message='Select an observation first.';render();return;}
  if(!Number.isFinite(nu)||nu<=0){message='Enter a positive exponent ν.';render();return;}
  message='Calculating M from drawing… Full-spectrum calculation may take time; cancel it in Numerical jobs.';render();
  try{
    const {result:job}=await api('/api/source-calibrations',{request_id:crypto.randomUUID(),observation_id:oid,nu});
    if(serial!==token){void api(`/api/jobs/${job.job_id}/cancel`,{}).catch(()=>{});return;}
    activeJob=job.job_id;const done=await watch(job);
    if(serial!==token||current!==key())return;
    activeJob=null;
    if(done.status!=='completed')throw Error(done.error?.message||`Source check ${done.status}.`);
    evidence=done.result;acceptedKey=current;cache.set(current,evidence);
    const prefix={compatible:'Compatible (numerically)',incompatible:'Warning',unresolved:'Unresolved'}[evidence.status]??'Source check';
    message=`${prefix}: ${evidence.reason}`;render();
  }catch(e){if(serial===token&&current===key()){activeJob=null;message='Calibration unavailable: '+e.message;render();}}
}
$('rq-M').addEventListener('input',render);
$('rq-nu').addEventListener('input',()=>{cancel();restore();render();});
$('rq-nu').addEventListener('blur',()=>{if($('calibrate-source').checked&&!evidence)void calculate();});
$('calibrate-source').addEventListener('change',()=>{
  if(!$('calibrate-source').checked){cancel();message='Automatic calibration is off; any compatible result already calculated remains available.';render();}
  else {restore();if(observation&&!evidence)void calculate();else render();}
});
$('rq-source-retry').onclick=()=>void calculate();
