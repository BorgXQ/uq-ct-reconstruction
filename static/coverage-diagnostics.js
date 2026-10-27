// Temporary, display-only diagnostics. Remove this module with its details panel.
const $=id=>document.getElementById(id);
const fmt=x=>Number.isFinite(x)?x.toPrecision(4):'unavailable';
export function clearCoverageDiagnostics(){
  $('coverage-diagnostic-summary').textContent='Available after repeated-trial validation completes.';
  $('coverage-diagnostics').ontoggle=null;
  for(const id of ['coverage-bias-plot','coverage-variance-plot'])$(id).replaceChildren();
}
export function renderCoverageDiagnostics(study,analysis){
  if(study.plan.template_id!==analysis.analysis_id||study.plan.iterations!==analysis.iteration)return;
  const maps=study.maps.corrected,s=study.summaries.corrected,n=study.plan.geometry.resolution;
  const truth=study.truth,bias=maps.mean?.map((v,i)=>v-truth[i]);
  const empirical=maps.empirical_variance,modeled=analysis.variance;
  const difference=empirical&&modeled?empirical.map((v,i)=>v-modeled[i]):null;
  const allowance=analysis.total_bias_allowance?.value;
  const actual=maps.actual_bias;
  const exceed=actual&&Number.isFinite(allowance)?actual.filter(v=>Math.abs(v)>allowance).length:null;
  const max=a=>a?.length?Math.max(...a.map(Math.abs)):null;
  const details=Array.from({length:n*n},(_,i)=>[
    fmt(bias?.[i]),fmt(actual?.[i]),fmt(empirical?.[i]),fmt(modeled?.[i]),
    modeled?.[i]>0&&empirical?fmt(empirical[i]/modeled[i]):'unavailable (zero or missing variance)',
    fmt(maps.coverage?.[i]),empirical&&s.numeric_centers>0?fmt(Math.sqrt(empirical[i]/s.numeric_centers)):'unavailable'
  ]);
  function draw(id,values,unit){
    const el=$(id);el.replaceChildren();if(!values){el.textContent='Unavailable: this statistic needs more returned reconstructions.';return;}
    const extent=max(values)||1e-12,canvas=document.createElement('canvas');canvas.width=canvas.height=n;
    canvas.setAttribute('role','img');canvas.setAttribute('aria-label',id==='coverage-bias-plot'?'Signed empirical bias map':'Empirical minus modeled variance map');
    const bytes=new Uint8ClampedArray(4*n*n);
    values.forEach((v,i)=>{const t=Math.min(1,Math.abs(v)/extent),end=v<0?[33,102,172]:[178,24,43];bytes.set([...end.map(c=>Math.round(247+(c-247)*t)),255],4*i);});
    canvas.getContext('2d').putImageData(new ImageData(bytes,n,n),0,0);
    const scale=document.createElement('div');scale.className='diagnostic-scale';
    scale.textContent=`${fmt(-extent)} ← 0 → ${fmt(extent)} ${unit}`;
    const legend=document.createElement('div');legend.className='diagnostic-gradient';
    const info=document.createElement('p');info.className='diagnostic-pixel';info.textContent=globalThis.matchMedia?.('(max-width:768px)').matches?'Tap a pixel for its values.':'Hover over a pixel for its values.';
    canvas.onmousemove=e=>{const box=canvas.getBoundingClientRect(),r=Math.min(n-1,Math.max(0,Math.floor((e.clientY-box.top)*n/box.height))),c=Math.min(n-1,Math.max(0,Math.floor((e.clientX-box.left)*n/box.width))),i=r*n+c,d=details[i];info.textContent=`Row ${r}, column ${c}: map ${fmt(values[i])}. Mean − truth ${d[0]}; noiseless bias ${d[1]}; empirical variance ${d[2]}; modeled variance ${d[3]}; ratio ${d[4]}; coverage ${d[5]}; SE of mean ${d[6]}.`;};
    canvas.onpointerup=e=>{if(e.pointerType==='touch'||e.pointerType==='pen')canvas.onmousemove(e);};
    el.append(canvas,legend,scale,info);
  }
  const paint=()=>{draw('coverage-bias-plot',bias,'Density');draw('coverage-variance-plot',difference,'Density²');};
  $('coverage-diagnostics').ontoggle=()=>{if($('coverage-diagnostics').open)paint();};
  if($('coverage-diagnostics').open)paint();
  $('coverage-diagnostic-summary').textContent=`K = ${analysis.iteration}; ${s.numeric_centers} returned reconstructions; ${s.returned_intervals} intervals. Shared bias allowance per pixel: ${fmt(allowance)}. Maximum |mean − truth|: ${fmt(max(bias))}. Maximum |noiseless bias|: ${fmt(max(actual))}. Pixels whose noiseless bias exceeds the allowance: ${exceed===null?'unavailable':exceed+' / '+n*n}. Approximate relative standard error of each sample variance: ${s.numeric_centers>1?(100*Math.sqrt(2/(s.numeric_centers-1))).toFixed(1)+'%':'unavailable'} under fixed-K Gaussian noise. Study ${study.study_id}.`;
}
