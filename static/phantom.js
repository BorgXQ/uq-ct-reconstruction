// Modified Shepp–Logan: additive ellipse amplitudes (Toft's contrast variant).
// Coordinates span [-1, 1], with positive y toward the top of the image.
// This is an editable 8-bit source; freezing uses the usual block-area conversion.
const ellipses = [
  [1,.69,.92,0,0,0],[-.8,.6624,.874,0,-.0184,0],
  [-.2,.11,.31,.22,0,-18],[-.2,.16,.41,-.22,0,18],
  [.1,.21,.25,0,.35,0],[.1,.046,.046,0,.1,0],
  [.1,.046,.046,0,-.1,0],[.1,.046,.023,-.08,-.605,0],
  [.1,.023,.023,0,-.606,0],[.1,.023,.046,.06,-.605,0]
];
function sheppLogan(side=512,original=false){
  if(!Number.isInteger(side)||side<2)throw new Error('Invalid phantom size');
  const values=new Float64Array(side*side);
  for(const [index,ellipse] of ellipses.entries()){
    const [modifiedAmplitude,a,b,cx,cy,degrees]=ellipse;
    const amplitude=original?[1,-.98,-.02,-.02,.01,.01,.01,.01,.01,.01][index]:modifiedAmplitude;
    const angle=degrees*Math.PI/180,cos=Math.cos(angle),sin=Math.sin(angle);
    for(let row=0;row<side;row++){
      const y=1-2*row/(side-1)-cy;
      for(let col=0;col<side;col++){
        const x=2*col/(side-1)-1-cx;
        if(((x*cos+y*sin)/a)**2+((-x*sin+y*cos)/b)**2<=1)values[row*side+col]+=amplitude;
      }
    }
  }
  return Uint8Array.from(values,v=>Math.round(255*Math.max(0,Math.min(1,v))));
}

export function modifiedSheppLogan(side=512){return sheppLogan(side);}
export function originalSheppLogan(side=512){return sheppLogan(side,true);}

// Deterministic test patterns, all editable through the same 8-bit drawing tools.
export function exampleImage(name,side=512){
  if(!Number.isInteger(side)||side<2)throw new Error('Invalid image size');
  if(name==='modified-shepp-logan')return modifiedSheppLogan(side);
  if(name==='original-shepp-logan')return originalSheppLogan(side);
  if(!['smooth-gradient','concentric-disks','asymmetric-disks'].includes(name))throw new Error('Unknown example image');
  const pixels=new Uint8Array(side*side);
  for(let row=0;row<side;row++)for(let col=0;col<side;col++){
    const x=2*col/(side-1)-1,y=1-2*row/(side-1);
    let value=0;
    if(name==='smooth-gradient')value=1-col/(side-1);
    if(name==='concentric-disks'){
      const radius=Math.hypot(x,y);
      value=radius<=.3?.25:radius<=.7?1:0;
    }
    if(name==='asymmetric-disks'){
      if(Math.hypot(x+.35,y-.25)<=.32)value=1;
      if(Math.hypot(x-.38,y+.2)<=.23)value=.5;
      if(Math.hypot(x+.15,y+.55)<=.12)value=.75;
    }
    pixels[row*side+col]=Math.round(255*value);
  }
  return pixels;
}
