// Display-only summaries. Never alter intervals, stopping or study membership.
export function ellipseCoverage(values,n,a,b){
  if(!(a>0&&a<=1&&b>0&&b<=1))throw Error('Ellipse radii must be greater than 0 and at most 1.');
  const mask=Array.from({length:n*n},(_,i)=>{const x=2*(i%n+.5)/n-1,y=1-2*(Math.floor(i/n)+.5)/n;return (x/a)**2+(y/b)**2<=1;});
  const selected=values?.filter((v,i)=>mask[i]&&Number.isFinite(v))??[];
  return {mask,count:mask.filter(Boolean).length,available:selected.length,mean:selected.length?selected.reduce((s,v)=>s+v,0)/selected.length:null};
}
export function rowPlot(svg,truth,mean,trial,n,row){
  svg.replaceChildren();if(!trial)return;
  const ns='http://www.w3.org/2000/svg',start=row*n,cut=a=>a?.slice(start,start+n),t=cut(truth),m=cut(mean),low=cut(trial.lower),high=cut(trial.upper),center=cut(trial.center);
  const values=[...t,...(m??[]),...center,...(low??[]),...(high??[])].filter(Number.isFinite);
  const min=Math.min(...values),max=Math.max(...values),pad=Math.max((max-min)*.08,.01),lo=min-pad,hi=max+pad;
  const X=i=>70+690*i/(n-1),Y=v=>250-220*(v-lo)/(hi-lo);
  const add=(tag,attrs,text)=>{const el=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,v);if(text!==undefined)el.textContent=text;svg.append(el);};
  for(let j=0;j<=4;j++){const v=lo+(hi-lo)*j/4;add('line',{x1:70,x2:760,y1:Y(v),y2:Y(v),stroke:'#e0e3e1'});add('text',{x:4,y:Y(v)+4,'font-size':12},v.toFixed(3));}
  if(low&&high)add('polygon',{points:[...high.map((v,i)=>`${X(i)},${Y(v)}`),...low.map((v,i)=>`${X(i)},${Y(v)}`).reverse()].join(' '),fill:'#aaa',opacity:.5,'data-role':'interval-band'});
  for(const [a,color,dash,role] of [[center,'#666','4 3','trial-center'],[t,'#111','','truth'],[m,'#c52b32','','mean']])if(a)add('polyline',{points:a.map((v,i)=>`${X(i)},${Y(v)}`).join(' '),fill:'none',stroke:color,'stroke-width':2,'stroke-dasharray':dash,'data-role':role});
  for(const i of [0,Math.floor((n-1)/2),n-1])add('text',{x:X(i),y:270,'text-anchor':'middle','font-size':12},String(i));
  add('text',{x:330,y:294,'font-size':13},`Column index · row ${row}`);add('text',{x:4,y:16,'font-size':12},'Density');
}
