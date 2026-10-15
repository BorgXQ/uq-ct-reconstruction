// Start app.py separately. Browser-only dev dependency; no frontend build.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const base = process.env.TEST_URL || 'http://127.0.0.1:5010';
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:1400,height:1200}});
const errors=[],external=[];
page.on('pageerror', error=>errors.push(error.message));
page.on('request', request=>{if(!request.url().startsWith(base))external.push(request.url());});
const wait = async()=>page.waitForFunction(()=>document.querySelector('#preview-status').textContent.includes('Backend conversion complete'));
const draft = async()=>{await page.click('#draft-tab');await wait();};
const shade = async value=>{await page.fill('#shade',String(value));await page.locator('#shade').press('Tab');};
const tool = async name=>page.click(`[data-tool=${name}]`);
const point = async(x,y)=>{const b=await page.locator('#editor').boundingBox();return{x:b.x+x*b.width/512,y:b.y+y*b.height/512};};
async function gesture(x,y,xx=x,yy=y,steps=2) {
  await page.locator('#editor').scrollIntoViewIfNeeded();
  const a=await point(x,y),b=await point(xx,yy);
  await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps});await page.mouse.up();
}
const pixel=async(x,y)=>page.locator('#editor').evaluate((canvas,{x,y})=>Array.from(canvas.getContext('2d').getImageData(x,y,1,1).data),{x,y});
const raster=async()=>page.locator('#editor').evaluate(canvas=>Array.from(canvas.getContext('2d').getImageData(0,0,512,512).data));
const metadata=async()=>JSON.parse(await page.locator('#metadata').textContent());
const getSnapshot=async id=>(await page.request.get(base+'/api/snapshots/'+id)).json();
async function clear(){await page.click('#clear');await draft();}
const check=message=>console.log('PASS '+message);
try {
  await page.goto(base);await wait();
  assert.equal(await page.locator('nav button:disabled').count(),3);
  assert.equal((await metadata()).statistics.maximum,0);
  assert.equal(await page.locator('#resolution').inputValue(),'32');
  check('local shell, actual blank draft, defaults, disabled future workspaces');

  await shade(128);await gesture(100,100);await draft();
  assert.deepEqual(await pixel(100,100),[128,128,128,255]);
  await gesture(100,100,400,100,1);await draft();
  for(const x of [120,250,380]) assert.equal((await pixel(x,100))[0],128);
  await page.fill('#row','6');await page.fill('#column','6');await page.click('#inspect');
  assert.match(await page.locator('#pixel-info').textContent(),/float64/);
  await tool('eraser');await gesture(250,100);await draft();assert.deepEqual(await pixel(250,100),[0,0,0,255]);
  await tool('brush');assert.equal(await page.locator('#shade').inputValue(),'128');
  check('arbitrary grayscale brush, continuous fast stroke, dab, eraser, pixel inspector');

  await clear();await tool('circle');await shade(192);await gesture(130,130,190,130);await draft();
  assert.equal((await pixel(130,130))[0],192);
  for(const [x,y] of [[180,130],[80,130],[130,80],[130,180]])assert.equal((await pixel(x,y))[0],192);
  for(const [x,y] of [[195,130],[130,195]])assert.equal((await pixel(x,y))[0],0);
  await tool('square');await shade(64);await page.selectOption('#shape-mode','outline');await gesture(260,260,420,380);await draft();
  assert.equal((await pixel(340,340))[0],0);assert.equal((await pixel(420,340))[0],64);
  assert.equal((await pixel(254,254))[0],64); // Sharp outer square corner, not a rounded rectangle.
  await tool('bucket');await shade(128);await gesture(340,340);await draft();
  assert.equal((await pixel(340,340))[0],128);assert.equal((await pixel(10,10))[0],0);
  const filled=await raster();await page.click('#undo');await draft();assert.equal((await pixel(340,340))[0],0);
  await page.click('#redo');await draft();assert.deepEqual(await raster(),filled);
  await tool('brush');await shade(137);await gesture(40,440,150,440);await draft();
  assert.equal((await pixel(90,440))[0],137);
  const demo = await raster();
  assert.ok((await metadata()).statistics.mean>0);
  // A non-palette stroke and shape edges must yield fractional area means.
  await page.click('#freeze');await page.waitForFunction(()=>document.querySelector('#freeze-status').textContent.startsWith('Frozen snapshot'));
  const first = await metadata(), stored=await getSnapshot(first.snapshot_id);
  assert.ok(stored.pixels.some(x=>x>0 && Math.abs(x*255-Math.round(x*255))>1e-8));
  const displayed=await page.locator('#numerical').evaluate(canvas=>Array.from(canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data));
  for(let i=0;i<stored.pixels.length;i++) {
    assert.equal(displayed[4*i],Math.round(255*stored.pixels[i]));
    assert.equal(displayed[4*i+3],255);
  }
  await page.fill('#row','8');await page.fill('#column','8');await page.click('#inspect');
  assert.ok((await page.locator('#pixel-info').textContent()).includes(stored.pixels[8*32+8].toPrecision(17)));
  await page.click('#clear');await page.click('#undo');await page.selectOption('#resolution','64');await draft();
  assert.deepEqual(await raster(),demo);assert.deepEqual(await getSnapshot(first.snapshot_id),stored);
  await page.click('#frozen-tab');assert.match(await page.locator('#preview-status').textContent(),/Differs/);
  assert.equal((await metadata()).resolution,32);
  await page.selectOption('#resolution','32');await draft();await page.click('#frozen-tab');
  assert.match(await page.locator('#preview-status').textContent(),/Matches current/);
  await page.selectOption('#resolution','64');await draft();await page.click('#freeze');
  await page.waitForFunction(id=>JSON.parse(document.querySelector('#metadata').textContent).snapshot_id!==id,first.snapshot_id);
  const second=await metadata();assert.equal(second.resolution,64);
  await page.selectOption('#snapshots',first.snapshot_id);
  await page.waitForFunction(id=>JSON.parse(document.querySelector('#metadata').textContent).snapshot_id===id,first.snapshot_id);
  assert.deepEqual((await getSnapshot(first.snapshot_id)).pixels,stored.pixels);
  await page.screenshot({path:'/tmp/ct-m1-desktop.png',fullPage:true});
  check('demonstration: circle 192, square outline 64, fill 128, stroke 137, fractional averages, two immutable snapshots, matching after undo');

  await page.click('#draft-tab');await clear();
  await tool('square');await page.selectOption('#shape-mode','filled');await shade(192);
  for(const [dx,dy] of [[40,20],[-40,20],[-20,-40],[20,-40]]) {
    await clear();await gesture(256,256,256+dx,256+dy);await draft();
    const x=dx<0?230:280,y=dy<0?230:280;assert.equal((await pixel(x,y))[0],192);
    assert.equal((await pixel(dx<0?210:302,y))[0],0);
  }
  await clear();await tool('bucket');await shade(64);await gesture(10,10);await draft();
  await tool('circle');await shade(192);await page.selectOption('#shape-mode','outline');await gesture(256,256,300,256);await draft();
  assert.equal((await pixel(256,256))[0],64);assert.equal((await pixel(300,256))[0],192);
  await page.selectOption('#shape-mode','filled');await gesture(256,256,290,256);await draft();assert.equal((await pixel(256,256))[0],192);
  const beforeNoOp=await page.locator('#revision').textContent();
  await gesture(100,100);assert.equal(await page.locator('#revision').textContent(),beforeNoOp);
  await tool('bucket');await shade(192);await gesture(256,256);assert.equal(await page.locator('#revision').textContent(),beforeNoOp);
  await page.fill('#shade','999');await page.locator('#shade').press('Tab');assert.equal(await page.locator('#shade').inputValue(),'192');
  // A numeric input owns Ctrl+Z; it must not undo the image.
  await page.locator('#shade').focus();await page.keyboard.press('Control+z');
  assert.equal(await page.locator('#revision').textContent(),beforeNoOp);await shade(192);
  const beforeCancel=await raster(), rev=await page.locator('#revision').textContent();
  await tool('brush');const p=await point(50,50),p2=await point(200,50);
  await page.mouse.move(p.x,p.y);await page.mouse.down();await page.mouse.move(p2.x,p2.y);
  assert.equal(await page.locator('#freeze').isDisabled(),true);
  await page.keyboard.press('Escape');await page.mouse.up();assert.deepEqual(await raster(),beforeCancel);assert.equal(await page.locator('#revision').textContent(),rev);
  await page.mouse.move(p.x,p.y);await page.mouse.down();await page.mouse.move(p2.x,p2.y);
  await page.locator('#editor').dispatchEvent('pointercancel',{pointerId:1});await page.mouse.up();assert.deepEqual(await raster(),beforeCancel);
  check('square quadrants, filled/outline semantics, Escape and pointer cancellation, gesture locking');

  await page.setViewportSize({width:540,height:1050});await clear();await tool('circle');await shade(137);await gesture(256,256,306,256);await draft();
  assert.equal((await pixel(256,300))[0],137);assert.equal((await pixel(300,256))[0],137);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  const beforeOverlay=await raster();await page.check('#grid');await page.click('#overlay');assert.deepEqual(await raster(),beforeOverlay);
  await page.locator('#overlay').focus();await page.keyboard.press('ArrowUp');
  assert.equal(await page.locator('#row').inputValue(),'30');
  await page.screenshot({path:'/tmp/ct-m1-mobile.png',fullPage:true});
  await page.setViewportSize({width:1400,height:1200});
  check('responsive pointer mapping, circle geometry, separate grid/selection overlay, no horizontal overflow');

  // Capture A at 16, hold response, then let B at 64 finish first.
  let releaseA, enteredA;const entered=new Promise(r=>enteredA=r),held=new Promise(r=>releaseA=r);
  await page.route('**/api/images/preview',async route=>{
    if(route.request().postDataJSON().numerical_resolution===16){const response=await route.fetch();enteredA();await held;await route.fulfill({response});}
    else await route.continue();
  });
  await page.selectOption('#resolution','16');await entered;
  await page.selectOption('#resolution','64');await draft();assert.equal((await metadata()).resolution,64);
  releaseA();await page.waitForTimeout(250);assert.equal((await metadata()).resolution,64);
  await page.unroute('**/api/images/preview');
  check('out-of-order preview and resolution responses rejected');

  // Latest preview failure must not make an older result current.
  let releaseOld, enteredOld;const oldEntered=new Promise(r=>enteredOld=r),oldHeld=new Promise(r=>releaseOld=r);
  await page.route('**/api/images/preview',async route=>{
    if(route.request().postDataJSON().numerical_resolution===16){const response=await route.fetch();enteredOld();await oldHeld;await route.fulfill({response});}
    else await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'Deliberate preview failure'}})});
  });
  await page.selectOption('#resolution','16');await oldEntered;await page.selectOption('#resolution','32');
  await page.waitForFunction(()=>document.querySelector('#preview-status').textContent.includes('Preview unavailable'));
  releaseOld();await page.waitForTimeout(250);assert.match(await page.locator('#preview-status').textContent(),/Preview unavailable/);
  assert.equal((await metadata()).resolution,64);
  await page.unroute('**/api/images/preview');await page.click('#retry');await draft();
  check('failed latest preview preserves old data with an explicit stale label and retry');

  let releaseFreeze, enteredFreeze;const freezeEntered=new Promise(r=>enteredFreeze=r),freezeHeld=new Promise(r=>releaseFreeze=r);
  const beforeFreeze=await metadata();
  await page.route('**/api/snapshots',async route=>{
    if(route.request().method()==='POST'){const response=await route.fetch();enteredFreeze();await freezeHeld;await route.fulfill({response});} else await route.continue();
  });
  await page.click('#freeze');await freezeEntered;await page.click('#clear');await draft();
  releaseFreeze();await page.waitForFunction(()=>document.querySelector('#freeze-status').textContent.startsWith('Frozen snapshot'));
  const captured=await metadata();assert.equal(captured.content_hash,beforeFreeze.content_hash);
  assert.match(await page.locator('#preview-status').textContent(),/earlier editor state/);
  await page.unroute('**/api/snapshots');
  const beforeFailure=await raster(),beforeId=captured.snapshot_id;
  await page.route('**/api/snapshots',async route=>{
    if(route.request().method()==='POST')await route.fulfill({status:507,contentType:'application/json',body:JSON.stringify({error:{message:'Deliberate storage failure'}})});
    else await route.continue();
  });
  await page.click('#freeze');await page.waitForFunction(()=>document.querySelector('#freeze-status').textContent.startsWith('Freeze failed'));
  assert.equal((await metadata()).snapshot_id,beforeId);assert.deepEqual(await raster(),beforeFailure);
  await page.unroute('**/api/snapshots');
  check('in-flight freeze captures earlier source; failed freeze preserves drawing and selected snapshot');

  await page.reload();await wait();await page.waitForFunction(()=>document.querySelector('#snapshots').options.length>1);
  await page.selectOption('#snapshots',first.snapshot_id);
  await page.waitForFunction(id=>JSON.parse(document.querySelector('#metadata').textContent).snapshot_id===id,first.snapshot_id);
  assert.deepEqual((await getSnapshot(first.snapshot_id)).pixels,stored.pixels);
  await page.route('**/api/snapshots/'+first.snapshot_id, route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:{message:'Snapshot unavailable after restart'}})}));
  await page.click('#refresh-snapshots');await page.waitForFunction(()=>document.querySelector('#preview-status').textContent.includes('Snapshot unavailable'));
  assert.equal(await page.locator('#empty-numerical').isVisible(),true);
  check('reload retrieves existing records; unavailable snapshots never become fake blank images');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  check('no browser exceptions or external runtime requests');
} finally {await browser.close();}
