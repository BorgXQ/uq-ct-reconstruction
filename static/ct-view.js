// Display transforms never change the numerical arrays.
export function sharedRange(a,b) {
  let low=Infinity,high=-Infinity;
  for(const values of [a,b])for(const x of values){low=Math.min(low,x);high=Math.max(high,x);}
  return low===high ? (low===0 ? [0,1] : [Math.min(0,low),Math.max(1,high)]) : [low,high];
}
export function sinogramFrame(values,angles,detectors,low,high) {
  const frame=new Uint8ClampedArray(4*values.length);
  for(let j=0;j<angles;j++)for(let ell=0;ell<detectors;ell++){
    const out=4*((detectors-1-ell)*angles+j),shade=Math.round(255*(values[j*detectors+ell]-low)/(high-low));
    frame[out]=frame[out+1]=frame[out+2]=shade;frame[out+3]=255;
  }
  return frame;
}
export function drawSinogram(canvas,values,na,nd,range) {
  canvas.width=na;canvas.height=nd;
  canvas.getContext('2d').putImageData(new ImageData(sinogramFrame(values,na,nd,...range),na,nd),0,0);
}
export function plot(svg,values,selected,label) {
  const ns='http://www.w3.org/2000/svg';svg.replaceChildren();
  function element(name,attrs,text){const e=document.createElementNS(ns,name);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;svg.append(e);return e;}
  if(!values.length)return;
  const maximum=Math.max(...values,1e-15),last=values.length-1,x=i=>62+470*i/Math.max(1,last),y=v=>195-165*v/maximum;
  element('path',{d:'M62 24 V195 H538',fill:'none',stroke:'#899c92'});
  element('polyline',{points:values.map((v,i)=>`${x(i)},${y(v)}`).join(' '),fill:'none',stroke:'#226754','stroke-width':2});
  for(const fraction of [0,.5,1]){element('text',{x:56,y:y(maximum*fraction)+4,'text-anchor':'end','font-size':11,fill:'#60726c'},(maximum*fraction).toPrecision(3));}
  element('text',{x:62,y:214,'font-size':11},'0');element('text',{x:538,y:214,'text-anchor':'end','font-size':11},String(last));
  element('text',{x:290,y:234,'text-anchor':'middle','font-size':11},'Completed iteration k');
  element('title',{},label);
  if(selected!==null)element('path',{d:`M${x(selected)} 24 V195`,stroke:'#cc7a28','stroke-dasharray':'4 4'});
}
