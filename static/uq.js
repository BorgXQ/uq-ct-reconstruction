'use strict';
let uqResult=null, uqPayload=null, uqCurrent=false, uqBusy=false, uqTimer=null;
const uf = value => value===null || value===undefined ? 'unavailable' : Number(value).toPrecision(5);
const uqIds=['uq-policy','uq-nu','uq-level','uq-c0','iterations','uq-bound','uq-stopping-mode'];
function uqValid(){
  const input=$('iterations');
  input.setCustomValidity(Number.isSafeInteger(Number(input.value))?'':'Enter an integer no larger than 9,007,199,254,740,991.');
  return uqIds.every(id=>$(id).reportValidity());
}
function uqSettings(){return {k_policy:$('uq-policy').value,mode:'oracle',
  bias_bound:$('uq-bound').value,stopping_mode:$('uq-stopping-mode').value,nu:Number($('uq-nu').value),level:Number($('uq-level').value),
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
  for(const id of ['uq-actual-bias','uq-spectral-bias','uq-global-bias'])$(id).textContent='—';
  $('uq-source').textContent='Source condition has not been checked for the current settings.';
  $('uq-stop-status').textContent='Awaiting current calculation.';$('uq-stop-values').textContent='';
  $('uq-status').textContent='Run Landweber above to calculate confidence intervals.';
  uqPlot();uqButtons();
}
uqIds.forEach(id=>$(id).addEventListener('input',()=>{$('iterations').setCustomValidity('');uqInvalidate();$('uq-status').textContent='Settings changed. Update intervals & stopping rule, or run Landweber again.';}));
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
  uqDisplay();$('uq-status').textContent=`Intervals for the ${result.config.n} × ${result.config.n} reconstruction at k = ${result.config.k}. Hover over the density graph or reconstruction to inspect a pixel.`;
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
$('uq-use-stop').onclick=()=>{if(!uqCurrent||!uqResult.stopping?.first)return;$('iterations').value=uqResult.stopping.first;$('uq-policy').value='fixed';$('uq-stopping-mode').value='manual';$('reconstruct').onclick();};
function uqDisplay() {
  const r=uqResult,c=r.config;
  $('uq-row').max=$('uq-col').max=c.n-1;
  $('uq-row').value=Math.min(Number($('uq-row').value),c.n-1);$('uq-col').value=Math.min(Number($('uq-col').value),c.n-1);
  $('uq-source').textContent=`${r.source.status.toUpperCase()}: ${r.source.reason} M = ${uf(r.source.M)} (${r.source.oracle?'calibrated from your drawing':'proposed assumption'}).`;
  $('uq-actual-bias').textContent=uf(r.diagnostics.bias_norm);
  $('uq-spectral-bias').textContent=uf(r.bounds.spectral);$('uq-global-bias').textContent=uf(r.bounds.global_bound);
  $('uq-interval-status').textContent=r.interval_status==='conditional'?`${100*c.level}% theoretical marginal coverage conditional on the source assumption and noise-independent K. Source compatibility passed numerical checks; these are not an exact-arithmetic proof.`:`${r.interval_status==='exploratory'?'Exploratory — no theoretical coverage guarantee. ':''}${r.interval_reason||''}`;
  const s=r.stopping;
  $('uq-stop-status').textContent=s?(s.first?`First satisfying iteration: K = ${s.first}.`:`Not reached within K = 1…${c.budget}.`):`Unavailable: ${r.interval_reason}`;
  if(s){
    const v=Math.max(...r.maps.sd.flat()),b=r.bounds.selected;
    $('uq-stop-values').textContent=`At displayed K = ${c.k}: B_K = ${uf(b)}, c₀ v_K = ${uf(s.c0*v)} — ${b<=s.c0*v?'condition met':'condition not yet met'}.`;
  } else $('uq-stop-values').textContent='';
  $('uq-mc-results').hidden=!r.mc;
  uqPlot();if(r.mc)uqMcDisplay();uqInspect();uqButtons();
}
function uqInspect() {
  const row=Number($('uq-row').value),col=Number($('uq-col').value);
  if(!Number.isInteger(row)||!Number.isInteger(col)||row<0||col<0)return;
  $('uq-pixel-label').textContent=`(${row}, ${col})`;
  if(!uqResult||!uqCurrent||row>=uqResult.config.n||col>=uqResult.config.n)return;
  uqHighlight(row,col);
  const r=uqResult,value=name=>r.maps[name]?.[row]?.[col];
  const interval=r.maps.lower?`${r.interval_status==='exploratory'?'Exploratory nominal ':''}${100*r.config.level}% interval [${uf(value('lower'))}, ${uf(value('upper'))}]`:'Confidence interval unavailable.';
  $('uq-inspector').textContent=`Reconstructed value: ${uf(value('reconstruction'))}. ${interval}`;
  const width=value('half_width'),share=v=>width>0?`${(100*v/width).toFixed(1)}%`:'0%';
  $('uq-components').textContent=r.maps.lower?`Half-width = ${uf(value('stochastic'))} (Gaussian: ${share(value('stochastic'))}) + ${uf(r.bounds.selected)} (${r.bounds.kind} bias: ${share(r.bounds.selected)}) = ${uf(width)}.` : '';
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

// The chart uses the already calculated maps; selecting a pixel makes no request.
let uqPlotScale=null;
function uqSvg(tag,attributes={},text) {
  const element=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attributes))element.setAttribute(key,value);
  if(text!==undefined)element.textContent=text;
  return element;
}
function uqPlot() {
  const svg=$('uq-density-plot');svg.replaceChildren();uqPlotScale=null;
  $('uq-pixel-box').hidden=true;
  if(!uqCurrent){
    svg.append(uqSvg('text',{x:500,y:165,'text-anchor':'middle',fill:'#a3b6c0'},'Run Landweber to display the density and confidence intervals.'));
    $('uq-plot-status').textContent='No current plot. Run Landweber or update the interval settings.';
    return;
  }
  const r=uqResult,n=r.config.n,values=r.maps.reconstruction.flat();
  const lower=r.maps.lower?.flat(),upper=r.maps.upper?.flat();
  let minimum=Math.min(...values,...(lower||[])),maximum=Math.max(...values,...(upper||[]));
  const padding=(maximum-minimum||Math.max(1,Math.abs(maximum)))*.06;
  minimum-=padding;maximum+=padding;
  const left=85,right=980,top=20,bottom=285;
  const x=i=>left+i/(values.length-1)*(right-left),y=v=>bottom-(v-minimum)/(maximum-minimum)*(bottom-top);
  uqPlotScale={left,right,top,bottom,x,y,values,n};
  for(let row=1;row<n;row++)svg.append(uqSvg('line',{x1:x(row*n-.5),x2:x(row*n-.5),y1:top,y2:bottom,stroke:'#283b45'}));
  for(let tick=0;tick<=4;tick++){
    const v=minimum+(maximum-minimum)*tick/4;
    svg.append(uqSvg('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#344854'}));
    svg.append(uqSvg('text',{x:left-10,y:y(v)+4,'text-anchor':'end',fill:'#a3b6c0'},Number(v.toPrecision(3)).toString()));
    const index=Math.round((values.length-1)*tick/4);
    svg.append(uqSvg('text',{x:x(index),y:307,'text-anchor':'middle',fill:'#a3b6c0'},index));
  }
  const path=series=>series.map((v,i)=>`${i?'L':'M'}${x(i)},${y(v)}`).join(' ');
  if(lower&&upper){
    const reverse=lower.map((v,i)=>`L${x(i)},${y(v)}`).reverse().join(' ');
    svg.append(uqSvg('path',{id:'uq-confidence-band',d:path(upper)+' '+reverse+' Z',fill:'#66d6c5','fill-opacity':'.22',stroke:'none'}));
    for(const bound of [lower,upper])svg.append(uqSvg('path',{d:path(bound),fill:'none',stroke:'#66d6c5','stroke-opacity':'.5','stroke-width':1}));
  }
  svg.append(uqSvg('path',{id:'uq-density-line',d:path(values),fill:'none',stroke:'#66d6c5','stroke-width':2}));
  svg.append(uqSvg('text',{x:530,y:333,'text-anchor':'middle',fill:'#bdcbd3'},'Pixel index (row by row)'));
  svg.append(uqSvg('text',{transform:'translate(18 155) rotate(-90)','text-anchor':'middle',fill:'#bdcbd3'},'Density'));
  svg.append(uqSvg('line',{id:'uq-plot-cursor',y1:top,y2:bottom,stroke:'#ffbf69','stroke-dasharray':'4 3'}));
  svg.append(uqSvg('circle',{id:'uq-plot-point',r:4,fill:'#ffbf69',stroke:'#10171c','stroke-width':1.5}));
  $('uq-plot-status').textContent=lower?`${r.interval_status==='exploratory'?'Exploratory nominal ':''}${100*r.config.level}% pixelwise intervals for all ${values.length} pixels; raw bounds are shown without clipping. This is not a simultaneous confidence band.`:`Reconstructed densities for all ${values.length} pixels. Confidence band unavailable: ${r.interval_reason||'no eligible intervals.'}`;
}
function uqHighlight(row,col) {
  if(!uqPlotScale)return;
  const {x,y,values,n}=uqPlotScale,index=row*n+col,box=$('uq-pixel-box');
  box.hidden=false;box.style.left=`${100*col/n}%`;box.style.top=`${100*row/n}%`;
  box.style.width=box.style.height=`${100/n}%`;
  $('uq-plot-cursor').setAttribute('x1',x(index));$('uq-plot-cursor').setAttribute('x2',x(index));
  $('uq-plot-point').setAttribute('cx',x(index));$('uq-plot-point').setAttribute('cy',y(values[index]));
  $('uq-density-plot').setAttribute('aria-label',`Density plot. Selected pixel ${index}, row ${row}, column ${col}, reconstructed density ${uf(values[index])}.`);
}
function uqSelectIndex(index) {
  if(!uqCurrent||!uqPlotScale)return;
  const n=uqPlotScale.n;index=Math.max(0,Math.min(n*n-1,index));
  $('uq-row').value=Math.floor(index/n);$('uq-col').value=index%n;uqInspect();
}
function uqSelectGraph(event) {
  if(!uqCurrent||!uqPlotScale)return;
  const svg=$('uq-density-plot'),point=new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());
  const {left,right,top,bottom,values}=uqPlotScale;
  if(point.x<left||point.x>right||point.y<top||point.y>bottom)return;
  uqSelectIndex(Math.round((point.x-left)/(right-left)*(values.length-1)));
}
$('uq-density-plot').onpointermove=uqSelectGraph;
$('uq-density-plot').onclick=uqSelectGraph;
$('uq-density-plot').onkeydown=event=>{
  if(!uqCurrent||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
  event.preventDefault();const n=uqResult.config.n,index=Number($('uq-row').value)*n+Number($('uq-col').value);
  const next={ArrowLeft:index-1,ArrowRight:index+1,ArrowUp:index-n,ArrowDown:index+n,Home:0,End:n*n-1};
  uqSelectIndex(next[event.key]);
};
uqPlot();
