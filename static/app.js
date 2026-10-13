'use strict';
const $ = id => document.getElementById(id);
const drawing = $('drawing'), ctx = drawing.getContext('2d', {willReadFrequently:true});
let tool = 'brush', drag = null, version = 0, projection = null, results = null, busy = false;
ctx.fillRect(0, 0, 512, 512);
function invalidate() {
  version++;
  document.body.classList.toggle('stale', !!projection);
  $('reconstruct').disabled = true;
  $('snapshot').disabled = true;
  $('status').textContent = projection ? 'Results are stale. Generate new projections for the current drawing, geometry, and noise settings.' : 'Drawing updated. Generate projections when ready.';
}
$('tools').addEventListener('click', e => {
  if (!e.target.dataset.tool) return;
  tool = e.target.dataset.tool;
  document.querySelectorAll('[data-tool]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.tool === tool)));
});
$('clear').onclick = () => {drag = null; ctx.fillStyle = 'black'; ctx.fillRect(0,0,512,512); invalidate();};
$('shade').oninput = () => {
  $('shade-value').textContent = $('shade').value;
  $('swatch').style.background = `rgb(${$('shade').value},${$('shade').value},${$('shade').value})`;
};
$('width').oninput = () => $('width-value').textContent = `${$('width').value} px`;
['angles', 'resolution', 'noise', 'noise-seed'].forEach(id => $(id).addEventListener('input', invalidate));
$('iterations').oninput = () => {
  if (results && projection?.version === version) $('status').textContent = `Showing saved run through k = ${results.history.length - 1}. Run Landweber to use the new iteration count.`;
};
function position(e) {
  const r = drawing.getBoundingClientRect();
  return {x:Math.max(0,Math.min(511.999,(e.clientX-r.left)*512/r.width)), y:Math.max(0,Math.min(511.999,(e.clientY-r.top)*512/r.height))};
}
function fill(point, shade) {
  const image = ctx.getImageData(0,0,512,512), data = image.data;
  const start = Math.floor(point.y)*512 + Math.floor(point.x), target = data[start*4];
  if (target === shade) return;
  // Four-connected fill; fixed seed tolerance prevents drift through soft edges.
  const seen = new Uint8Array(512*512), queue = new Int32Array(512*512);
  let head=0, tail=1; queue[0]=start; seen[start]=1;
  while (head < tail) {
    const p=queue[head++];
    if (Math.abs(data[p*4]-target)>8) continue;
    data[p*4]=data[p*4+1]=data[p*4+2]=shade;
    const neighbors=[];
    if(p%512>0) neighbors.push(p-1);
    if(p%512<511) neighbors.push(p+1);
    if(p>=512) neighbors.push(p-512);
    if(p<512*511) neighbors.push(p+512);
    for(const q of neighbors) if(!seen[q]) {seen[q]=1;queue[tail++]=q;}
  }
  ctx.putImageData(image,0,0);
}
function stroke(a,b) {
  ctx.strokeStyle=ctx.fillStyle=drag.color; ctx.lineWidth=drag.width; ctx.lineCap='round';
  ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
  ctx.beginPath(); ctx.arc(b.x,b.y,drag.width/2,0,2*Math.PI); ctx.fill();
}
function updateDrag(p) {
  if (drag.tool==='brush'||drag.tool==='eraser') stroke(drag.last,p);
  else {
    ctx.putImageData(drag.before,0,0); ctx.fillStyle=drag.color;
    const dx=p.x-drag.start.x, dy=p.y-drag.start.y;
    if(drag.tool==='circle') {ctx.beginPath();ctx.arc(drag.start.x,drag.start.y,Math.hypot(dx,dy),0,2*Math.PI);ctx.fill();}
    else {const side=Math.max(Math.abs(dx),Math.abs(dy));ctx.fillRect(drag.start.x,drag.start.y,(dx<0?-1:1)*side,(dy<0?-1:1)*side);}
  }
  drag.last=p;
}
drawing.onpointerdown = e => {
  if(e.button!==0 || drag) return;
  e.preventDefault();
  const p=position(e), shade=Number($('shade').value);
  invalidate();
  if(tool==='fill') {fill(p,shade);return;}
  drag={tool,start:p,last:p,width:Number($('width').value),color:tool==='eraser'?'black':`rgb(${shade},${shade},${shade})`,before:ctx.getImageData(0,0,512,512)};
  drawing.setPointerCapture(e.pointerId);updateDrag(p);
};
drawing.onpointermove = e => {if(drag) updateDrag(position(e));};
drawing.onpointerup = e => {if(drag){updateDrag(position(e));drag=null;}};
drawing.onpointercancel = () => {if(drag){ctx.putImageData(drag.before,0,0);drag=null;}};
function sample(n) {
  const data=ctx.getImageData(0,0,512,512).data, block=512/n;
  return Array.from({length:n},(_,row)=>Array.from({length:n},(_,col)=>{
    let total=0;
    for(let y=row*block;y<(row+1)*block;y++) for(let x=col*block;x<(col+1)*block;x++) total+=data[(y*512+x)*4];
    return total/(block*block*255);
  }));
}
function render(canvas, values, scale=1, minimum=0) {
  const h=values.length,w=values[0].length, off=document.createElement('canvas');off.width=w;off.height=h;
  const c=off.getContext('2d'), img=c.createImageData(w,h);
  values.forEach((row,y)=>row.forEach((v,x)=>{const i=(y*w+x)*4;img.data[i]=img.data[i+1]=img.data[i+2]=Math.round(255*Math.max(0,Math.min(1,(v-minimum)/scale)));img.data[i+3]=255;}));
  c.putImageData(img,0,0);const out=canvas.getContext('2d');out.imageSmoothingEnabled=false;out.drawImage(off,0,0,canvas.width,canvas.height);
}
function clearResults() {
  results=null; $('snapshot').disabled=true; $('snapshot').max=0;
  for(const id of ['reconstruction','graph']) {const c=$(id);c.getContext('2d').clearRect(0,0,c.width,c.height);}
  for(const id of ['residual','error']) $(id).textContent='—';
  $('iteration-label').textContent='k = 0';
}
async function request(action,payload) {
  const response=await fetch(`/api/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();if(!response.ok) throw new Error(data.error || 'Calculation failed.');return data;
}
function setBusy(value) {
  busy=value;$('project').disabled=value;$('reconstruct').disabled=value||!projection||projection.version!==version;
  $('snapshot').disabled=value||!results||projection?.version!==version;
  drawing.style.pointerEvents=value?'none':'';
  document.querySelectorAll('.controls input,.controls select,.tools button').forEach(el=>el.disabled=value);
}
$('project').onclick = async () => {
  if(busy || !['angles','noise','noise-seed'].every(id => $(id).reportValidity())) return;
  const current={image:sample(Number($('resolution').value)),angles:Number($('angles').value),noise_std:Number($('noise').value),noise_seed:Number($('noise-seed').value),version};
  setBusy(true);$('status').textContent='Building ray–pixel intersections and calculating projections…';
  try {
    const data=await request('project',current);projection=current;clearResults();
    let peak=0, minimum=0;
    for(const row of data.sinogram) for(const value of row) {peak=Math.max(peak,value);minimum=Math.min(minimum,value);}
    render($('sinogram'),data.sinogram,(peak-minimum)||1,minimum);
    $('geometry').textContent=`${current.angles} angles × ${data.offsets.length} detectors`;
    $('sino-axis').textContent=`θ → 0° to ${data.angles.at(-1).toFixed(1)}° · s ↓ ${data.offsets[0].toFixed(2)} to ${data.offsets.at(-1).toFixed(2)}`;
    $('sino-scale').textContent=`Display: black = ${minimum.toPrecision(4)}, white = ${peak.toPrecision(4)} line-integral units`;
    $('step').textContent=data.step.toPrecision(5);
    $('experiment').textContent=`${current.image.length} × ${current.image.length} grid · ${current.angles} equally spaced angles in [0°, 180°) · noise σ = ${current.noise_std}, seed = ${current.noise_seed}.`;
    document.body.classList.remove('stale');$('status').textContent='Projections ready. Run Landweber to reconstruct this image.';
  } catch(error){$('status').textContent=error.message;} finally {setBusy(false);}
};
$('reconstruct').onclick = async () => {
  if(busy||!projection||projection.version!==version||!$('iterations').reportValidity()) return;
  setBusy(true);$('status').textContent=`Computing ${$('iterations').value} Landweber iterations…`;
  try {
    results=await request('reconstruct',{...projection,iterations:Number($('iterations').value)});
    $('snapshot').max=results.snapshots.length-1;$('snapshot').value=results.snapshots.length-1;
    showSnapshot();$('status').textContent=`Reconstruction ready. Inspect ${results.snapshots.length} saved iterates using the slider.`;
  } catch(error){$('status').textContent=error.message;} finally {setBusy(false);}
};
function showSnapshot() {
  if(!results) return;
  const snapshot=results.snapshots[Number($('snapshot').value)], metrics=results.history[snapshot.iteration];
  render($('reconstruction'),snapshot.image);$('iteration-label').textContent=`k = ${snapshot.iteration}`;
  $('residual').textContent=metrics.residual.toExponential(4);
  $('error').textContent=metrics.error===null?'N/A — zero ground truth':`${(100*metrics.error).toFixed(2)}%`;
  graph(snapshot.iteration);
}
$('snapshot').oninput=showSnapshot;
function graph(selected) {
  const c=$('graph').getContext('2d'), w=760,h=220,p=35, history=results.history, last=history.length-1;
  c.clearRect(0,0,w,h);c.font='12px system-ui';c.fillStyle='#a3b6c0';c.strokeStyle='#344854';
  const maximum=Math.max(1,...history.map(v=>v.error??0));
  for(const value of [0,maximum/2,maximum]) {const y=h-p-value/maximum*(h-2*p);c.beginPath();c.moveTo(p,y);c.lineTo(w-p,y);c.stroke();c.fillText(value.toFixed(1),6,y+4);}
  const initial=history[0].residual;
  for(const [key,color] of [['residual','#66d6c5'],['error','#edb569']]) {
    c.strokeStyle=color;c.beginPath();history.forEach((v,i)=>{if(v[key]===null)return;const value=key==='residual'?(initial?v[key]/initial:0):v[key];const x=p+i/last*(w-2*p),y=h-p-value/maximum*(h-2*p);if(i===0)c.moveTo(x,y);else c.lineTo(x,y);});c.stroke();
  }
  c.strokeStyle='#ecf4f9';const x=p+selected/last*(w-2*p);c.beginPath();c.moveTo(x,p);c.lineTo(x,h-p);c.stroke();
  c.fillText('0',p,h-10);c.fillText(`Iteration ${last}`,w-p-95,h-10);
}
