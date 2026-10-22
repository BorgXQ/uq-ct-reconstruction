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
export function modifiedSheppLogan(side=512){
  if(!Number.isInteger(side)||side<2)throw new Error('Invalid phantom size');
  const values=new Float64Array(side*side);
  for(const [amplitude,a,b,cx,cy,degrees] of ellipses){
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
