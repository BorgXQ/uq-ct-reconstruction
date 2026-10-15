// Pure display mapping; raw numerical arrays are never modified.
export function limits(values) {
  let low=Infinity,high=-Infinity;
  for(const x of values){low=Math.min(low,x);high=Math.max(high,x);}
  return [low,high];
}
export function mapBytes(values,range,{signed=false}={}) {
  const [low,high]=range,bytes=new Uint8ClampedArray(values.length*4);
  values.forEach((x,i)=>{
    const t=high===low?.5:Math.max(0,Math.min(1,(x-low)/(high-low)));
    if(signed){bytes[i*4]=Math.round(255*t);bytes[i*4+1]=Math.round(200*(1-Math.abs(2*t-1)));bytes[i*4+2]=Math.round(255*(1-t));}
    else bytes[i*4]=bytes[i*4+1]=bytes[i*4+2]=Math.round(255*t);
    bytes[i*4+3]=255;
  });
  return bytes;
}
export function stoppingPlot(svg,history,selected) {
  svg.replaceChildren();if(!history.length)return;
  const ns='http://www.w3.org/2000/svg',width=600,height=240,pad=38;
  const finite=history.flatMap(h=>[h.bias_bound,h.threshold]).filter(Number.isFinite);
  const high=Math.max(...finite,0)||1,end=history.at(-1).iteration;
  const X=k=>pad+(width-2*pad)*(k-1)/Math.max(1,end-1),Y=v=>height-pad-(height-2*pad)*v/high;
  const node=(tag,attrs,text)=>{const el=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);if(text!==undefined)el.textContent=text;svg.append(el);return el;};
  node('path',{d:`M${pad} ${pad}V${height-pad}H${width-pad}`,stroke:'#a3b3ab',fill:'none'});
  for(const [key,color] of [['bias_bound','#be7026'],['threshold','#28776a']]){
    let points=[];const flush=()=>{if(points.length)node('polyline',{points:points.join(' '),stroke:color,fill:'none','stroke-width':2});points=[];};
    for(const h of history){if(Number.isFinite(h[key]))points.push(`${X(h.iteration)},${Y(h[key])}`);else flush();}flush();
  }
  if(selected!==null)node('path',{d:`M${X(selected)} ${pad}V${height-pad}`,stroke:'#303d37','stroke-dasharray':'4 3'});
  node('text',{x:3,y:pad,'font-size':11},high.toPrecision(3));node('text',{x:15,y:height-pad,'font-size':11},'0');
  node('text',{x:pad,y:height-15,'font-size':11},'k = 1');node('text',{x:width-pad-50,y:height-15,'font-size':11},`k = ${end}`);
}
