import {copyFile,mkdir} from 'node:fs/promises';
const source=new URL('../node_modules/plotly.js-basic-dist-min/',import.meta.url);
const target=new URL('../static/vendor/',import.meta.url);
await mkdir(target,{recursive:true});
await copyFile(new URL('plotly-basic.min.js',source),new URL('plotly-basic.min.js',target));
await copyFile(new URL('LICENSE',source),new URL('plotly-LICENSE',target));
