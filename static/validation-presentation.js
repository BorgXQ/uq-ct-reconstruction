// Display-only summaries. Never alter intervals, stopping or study membership.
export function ellipseCoverage(values,n,a,b){
  if(!(a>0&&a<=1&&b>0&&b<=1))throw Error('Ellipse radii must be greater than 0 and at most 1.');
  const mask=Array.from({length:n*n},(_,i)=>{const x=2*(i%n+.5)/n-1,y=1-2*(Math.floor(i/n)+.5)/n;return (x/a)**2+(y/b)**2<=1;});
  const selected=values?.filter((v,i)=>mask[i]&&Number.isFinite(v))??[];
  return {mask,count:mask.filter(Boolean).length,available:selected.length,mean:selected.length?selected.reduce((s,v)=>s+v,0)/selected.length:null};
}
// Pure Plotly specification: every trace uses raw values from the same row.
export function rowFigure(truth,trial,n,row,alpha=.05){
  const start=row*n,cut=a=>a?.slice(start,start+n),x=Array.from({length:n},(_,i)=>i);
  const t=cut(truth),center=cut(trial.center),low=cut(trial.lower),high=cut(trial.upper);
  const level=Number(((1-alpha)*100).toFixed(2)),data=[];
  if(low&&high){
    data.push({type:'scatter',mode:'lines',x,y:low,name:'Lower bound',showlegend:false,
      legendgroup:'interval',line:{width:0,simplify:false},hoverinfo:'skip',meta:{role:'interval-lower'}});
    data.push({type:'scatter',mode:'lines',x,y:high,name:`${level}% Confidence interval`,
      legendgroup:'interval',legendrank:3,line:{width:0,color:'#a3a3a3',simplify:false},
      fill:'tonexty',fillcolor:'rgba(150,150,150,0.30)',hoverinfo:'skip',meta:{role:'interval-upper'}});
  }
  if(t)data.push({type:'scatter',mode:'lines',x,y:t,name:'Truth',legendrank:1,
    line:{color:'#111',width:2,simplify:false},meta:{role:'truth'},
    hovertemplate:'Column %{x}<br>Density %{y:.5f}<extra>Truth</extra>'});
  data.push({type:'scatter',mode:'lines',x,y:center,name:'Landweber mean',legendrank:2,
    line:{color:'#c52b32',width:2,simplify:false},meta:{role:'trial-center'},
    customdata:low&&high?low.map((v,i)=>[v,high[i]]):undefined,
    hovertemplate:'Column %{x}<br>Density %{y:.5f}'+(low&&high?`<br>${level}% interval [%{customdata[0]:.5f}, %{customdata[1]:.5f}]`:'')+'<extra>Landweber mean</extra>'});
  const values=[...(t??[]),...center,...(low??[]),...(high??[])].filter(Number.isFinite);
  const min=Math.min(...values),max=Math.max(...values),pad=Math.max((max-min)*.08,.01);
  return {data,layout:{
    margin:{l:58,r:16,t:12,b:48},paper_bgcolor:'#fffef8',plot_bgcolor:'#fffef8',
    font:{family:'system-ui, sans-serif',size:12,color:'#19312e'},
    xaxis:{title:{text:`Column index · row ${row}`,standoff:8},range:[0,Math.max(1,n-1)],
      dtick:n<=24?Math.max(1,Math.ceil(n/5)):undefined,showgrid:false,zeroline:false,
      showline:true,linecolor:'#bdc7c1',ticks:'outside',tickcolor:'#bdc7c1'},
    yaxis:{title:{text:'Density',standoff:8},range:[min-pad,max+pad],
      gridcolor:'#e5e9e5',zeroline:false,nticks:5,automargin:true},
    legend:{x:.99,y:.99,xanchor:'right',yanchor:'top',traceorder:'normal',
      bgcolor:'rgba(255,254,248,0.94)',bordercolor:'#bdc7c1',borderwidth:1,font:{size:11}},
    hovermode:'x unified',dragmode:'zoom',
    uirevision:`${trial.analysis_id??trial.trial_index??''}:${trial.iteration??trial.k??''}:${row}`,
    annotations:low&&high?[]:[{text:'Confidence interval unavailable',xref:'paper',yref:'paper',
      x:0,y:1,showarrow:false,xanchor:'left',yanchor:'top',font:{size:11,color:'#60726c'}}],
  }};
}

export function rowPlot(plot,truth,trial,n,row,alpha=.05){
  if(!trial){globalThis.Plotly.purge(plot);plot.replaceChildren();return;}
  const {data,layout}=rowFigure(truth,trial,n,row,alpha);
  layout.width=plot.clientWidth||800;layout.height=plot.clientHeight||300;
  // The mean is T_K y for this observation, not a repeated-trial average.
  return globalThis.Plotly.react(plot,data,layout,{responsive:true,displaylogo:false,
    scrollZoom:false,displayModeBar:'hover',modeBarButtonsToRemove:['select2d','lasso2d'],
    toImageButtonOptions:{format:'png',filename:`confidence-interval-row-${row}`,scale:2}});
}
