import {api,watch} from './ct.js';
const $=id=>document.getElementById(id);
let observation='',token=0,evidence=null,activeJob=null,notify=()=>{};
const key=()=>`${$('rq-observation').value}:${$('rq-nu').value}`;
let acceptedKey='';
export function sourceReady(){return evidence?.status==='compatible'&&acceptedKey===key();}
export function calibratedSource(){
  if(!sourceReady())throw Error('Wait for compatible source calibration before running reconstruction.');
  return {M:evidence.M,nu:evidence.nu,provenance:`Drawing ${evidence.snapshot_id}; full-normal spectral calibration; numerical evidence only.`};
}
function invalidate(message){evidence=null;acceptedKey='';$('rq-M').value='';$('rq-source-status').textContent=message;notify();}
export function observationChanged(callback){
  notify=callback;
  const next=$('rq-observation').value;
  if(next!==observation){observation=next;void calculate();}
}
async function calculate(){
  const current=key(),serial=++token,oid=$('rq-observation').value,nu=Number($('rq-nu').value);
  if(activeJob){void api(`/api/jobs/${activeJob}/cancel`,{}).catch(()=>{});activeJob=null;}
  if(!oid){invalidate('Select an acquisition to check the source condition.');return;}
  if(!Number.isFinite(nu)||nu<=0){invalidate('Enter a positive exponent ν.');return;}
  invalidate('Checking source condition… Full-spectrum calculation may take time; progress and cancellation are in Numerical jobs.');
  try{
    const {result:job}=await api('/api/source-calibrations',{request_id:crypto.randomUUID(),observation_id:oid,nu});
    if(serial!==token){void api(`/api/jobs/${job.job_id}/cancel`,{}).catch(()=>{});return;}
    activeJob=job.job_id;const done=await watch(job);
    if(serial!==token||current!==key())return;
    activeJob=null;
    if(done.status!=='completed')throw Error(done.error?.message||`Source check ${done.status}.`);
    evidence=done.result;acceptedKey=current;
    $('rq-M').value=evidence.M??'';
    const prefix={compatible:'Compatible (numerically)',incompatible:'Warning',unresolved:'Unresolved'}[evidence.status];
    $('rq-source-status').textContent=`${prefix}: ${evidence.reason}`;
    notify();
  }catch(e){if(serial===token&&current===key())invalidate('Unresolved: '+e.message);}
}
$('rq-nu').addEventListener('input',()=>{++token;invalidate('Exponent changed; leave the field to recalculate M.');});
$('rq-nu').addEventListener('blur',()=>{if(!sourceReady())void calculate();});
$('rq-source-retry').onclick=()=>void calculate();
