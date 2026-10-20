// Viridis stops from the locally vendored Plotly color scale.
const stops = [[0,"#440154"],[.06274509803921569,"#48186a"],[.12549019607843137,"#472d7b"],[.18823529411764706,"#424086"],[.25098039215686274,"#3b528b"],[.3137254901960784,"#33638d"],[.3764705882352941,"#2c728e"],[.4392156862745098,"#26828e"],[.5019607843137255,"#21918c"],[.5647058823529412,"#1fa088"],[.6274509803921569,"#28ae80"],[.6901960784313725,"#3fbc73"],[.7529411764705882,"#5ec962"],[.8156862745098039,"#84d44b"],[.8784313725490196,"#addc30"],[.9411764705882353,"#d8e219"],[1,"#fde725"]];
export const viridisGradient='linear-gradient(to top,'+stops.map(([t,c])=>`${c} ${100*t}%`).join(',')+')';
const rgb=stops.map(([t,c])=>[t,...[1,3,5].map(i=>parseInt(c.slice(i,i+2),16))]);
export function coverageBytes(values){
  const bytes=new Uint8ClampedArray(values.length*4);
  values.forEach((value,i)=>{
    let color=[230,230,230];
    if(Number.isFinite(value)){
      const t=Math.max(0,Math.min(1,value));let j=1;
      while(j<rgb.length-1&&rgb[j][0]<t)j++;
      const a=rgb[j-1],b=rgb[j],f=(t-a[0])/(b[0]-a[0]);
      color=[1,2,3].map(k=>Math.round(a[k]+f*(b[k]-a[k])));
    }
    bytes.set([...color,255],i*4);
  });
  return bytes;
}
