import {Editor, paintRaster} from './editor.js';
import {base64} from './editor-core.js';
const $ = id => document.getElementById(id);
const documentId = crypto.randomUUID();
let generation = 0, requestSerial = 0, timer, draft = null, draftGeneration = -1;
let previewState = 'pending', frozen = null, mode = 'draft';
let busy = false, freezing = false, selectionSerial = 0, frozenUnavailable = false;
const editor = new Editor($('editor'), changed, value => { busy = value; controls(); });

function report(message) { $('errors').textContent = message; $('errors').hidden = !message; }
async function api(path, body) {
  const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status}).`);
  return data;
}
function capture() {
  return {request_id: `${documentId}:${++requestSerial}`, editor_document_id: documentId,
    editor_revision: editor.history.revision, source_side: 512, source_encoding: 'grayscale_u8_base64',
    source_data: base64(editor.history.pixels.slice()), numerical_resolution: Number($('resolution').value),
    preprocessing_version: 'block_area_v1'};
}
function controls() {
  $('paint-controls').disabled = busy;
  $('resolution').disabled = busy;
  $('freeze').disabled = busy || freezing;
  $('freeze').textContent = freezing ? 'Freezing…' : 'Freeze image';
  $('undo').disabled = busy || !editor.history.undoStack.length;
  $('redo').disabled = busy || !editor.history.redoStack.length;
  $('clear').disabled = busy;
  $('revision').textContent = `Revision ${editor.history.revision}`;
}
function changed() {
  generation++; previewState = 'pending'; controls(); render();
  clearTimeout(timer); timer = setTimeout(preview, 150);
}
async function preview() {
  clearTimeout(timer);
  const input = capture(), expectedGeneration = generation, serial = input.request_id;
  latestPreview = serial; previewState = 'pending'; render();
  try {
    const result = await api('/api/images/preview', input);
    if (expectedGeneration !== generation || latestPreview !== serial) return;
    if (result.request_id !== serial || result.editor_document_id !== documentId ||
        result.editor_revision !== input.editor_revision || result.resolution !== input.numerical_resolution ||
        result.preprocessing_version !== input.preprocessing_version) throw new Error('Preview response does not match the requested input. Retry.');
    draft = result; draftGeneration = expectedGeneration; previewState = 'ready';
    $('retry').hidden = true; report(''); render();
  } catch (error) {
    if (expectedGeneration !== generation || latestPreview !== serial) return;
    previewState = 'error'; $('retry').hidden = false; report(`Preview failed: ${error.message}`); render();
  }
}
let latestPreview = null;
function matches() {
  if (!frozen) return 'No frozen snapshot selected.';
  if (previewState !== 'ready' || draftGeneration !== generation) return 'Checking current draft against frozen snapshot…';
  return frozen.source_hash === draft.source_hash && frozen.content_hash === draft.content_hash
    ? 'Matches current editor and numerical settings.' : 'Frozen from an earlier editor state · Differs from current editor.';
}
function current() { return mode === 'draft' ? draft : frozenUnavailable ? null : frozen; }
function render() {
  const data = current();
  $('draft-tab').setAttribute('aria-pressed', mode === 'draft');
  $('frozen-tab').setAttribute('aria-pressed', mode === 'frozen');
  $('preview-status').textContent = mode === 'draft'
    ? (previewState === 'ready' && draftGeneration === generation ? 'Current draft · Backend conversion complete.'
      : previewState === 'error' ? 'Preview unavailable for current input · Showing previous input, if available.' : 'Updating · Showing previous input, if available.')
    : frozenUnavailable ? 'Snapshot unavailable · Refresh the list or freeze a new image.' : matches();
  $('empty-numerical').hidden = Boolean(data);
  if (!data) {
    $('empty-numerical').textContent = mode === 'frozen' ? 'Select or freeze a numerical snapshot.' : 'Waiting for the backend conversion…';
    $('dimensions').textContent = 'No image';
  } else {
    const n = data.resolution;
    paintRaster($('numerical'), Uint8Array.from(data.pixels, x => Math.round(255*x)), n);
    $('dimensions').textContent = `${n} × ${n}`;
  }
  overlay();
  window.dispatchEvent(new CustomEvent('ct-draft', {detail: {ready: previewState === 'ready' && draftGeneration === generation, source_hash: draft?.source_hash, content_hash: draft?.content_hash}}));
}
function overlay() {
  const canvas = $('overlay'), ctx = canvas.getContext('2d'), data = current(); ctx.clearRect(0, 0, 512, 512);
  if (!data) return;
  const cell = 512 / data.resolution;
  if ($('grid').checked) {
    ctx.strokeStyle = 'rgba(160,190,180,.4)'; ctx.lineWidth = .65; ctx.beginPath();
    for (let i=1; i<data.resolution; i++) { ctx.moveTo(i*cell,0);ctx.lineTo(i*cell,512);ctx.moveTo(0,i*cell);ctx.lineTo(512,i*cell); } ctx.stroke();
  }
}
async function refreshSnapshots(keepId = frozen?.snapshot_id) {
  const result = await api('/api/snapshots');
  $('snapshots').replaceChildren(new Option('Select a frozen snapshot', ''));
  for (const [i, item] of result.snapshots.entries()) {
    $('snapshots').add(new Option(`${i+1} · ${item.resolution} × ${item.resolution} · ${item.snapshot_id.slice(0,8)}`, item.snapshot_id));
  }
  if (keepId && result.snapshots.some(item => item.snapshot_id === keepId)) $('snapshots').value = keepId;
  else if (keepId) { frozenUnavailable = true; render(); }
  window.dispatchEvent(new CustomEvent('ct-snapshots', {detail: {records: result.snapshots, selectedId: keepId}}));
}
async function selectSnapshot(id) {
  const serial = ++selectionSerial;
  if (!id) { frozen = null; frozenUnavailable = false; mode = 'frozen'; render(); return; }
  try {
    const result = await api(`/api/snapshots/${encodeURIComponent(id)}`);
    if (serial !== selectionSerial) return;
    frozen = result; frozenUnavailable = false; mode = 'frozen'; report(''); render();
    window.dispatchEvent(new CustomEvent('ct-snapshot-selected', {detail: result.snapshot_id}));
  } catch (error) { if(serial === selectionSerial) { frozenUnavailable = true; mode='frozen'; report(error.message); render(); } }
}
$('freeze').onclick = async () => {
  if (busy || freezing) return;
  const input = capture(); freezing = true; controls(); $('freeze-status').textContent = 'Saving captured source and numerical settings…';
  try {
    const result = await api('/api/snapshots', input);
    if (result.request_id !== input.request_id || result.resolution !== input.numerical_resolution || !result.snapshot_id) throw new Error('Unexpected freeze response. Refresh the snapshot list.');
    ++selectionSerial; frozen = result; frozenUnavailable = false; mode = 'frozen';
    report(''); $('freeze-status').textContent = `Frozen snapshot ${result.snapshot_id.slice(0,8)} · Stored in this server process.`; render();
    try { await refreshSnapshots(result.snapshot_id); } catch(error) { report(`Image frozen, but list refresh failed: ${error.message}`); }
  } catch(error) { $('freeze-status').textContent = 'Freeze failed. Previous snapshots preserved; use Freeze image to retry.'; report(error.message); }
  finally { freezing = false; controls(); }
};
const hints = {brush:'Drag to paint. Click for a round dab. Escape cancels a gesture.', eraser:'Paints black with the selected thickness. Your painting shade is preserved.', circle:'Press at the center, then drag outward to set the radius. Escape cancels.', square:'Press at one corner and drag to size an equal-sided square. Escape cancels.'};
document.querySelectorAll('[data-tool]').forEach(button => button.onclick = () => {
  if(busy) return;
  editor.settings.tool = button.dataset.tool;
  document.querySelectorAll('[data-tool]').forEach(b => b.setAttribute('aria-pressed', b === button));
  $('tool-hint').textContent = hints[button.dataset.tool];
});
function setShade(shade) {
  editor.settings.shade = shade; $('shade').value = $('shade-slider').value = shade;
  $('swatch').style.background = `rgb(${shade} ${shade} ${shade})`;
  $('shade-value').textContent = `${shade} / 255 ≈ ${(shade/255).toFixed(6)}`;
}
function integerControl(id, key, min, max, callback) {
  $(id).addEventListener('change', () => {
    const text = $(id).value, value = Number(text);
    if (text.trim() === '' || !Number.isInteger(value) || value < min || value > max) {
      $(id).value = editor.settings[key]; report(`${id}: enter an integer from ${min} to ${max}.`); return;
    }
    editor.settings[key] = value; callback?.(value);
  });
}
integerControl('shade','shade',0,255,setShade); integerControl('thickness','thickness',1,128);
$('shade-slider').oninput = () => setShade(Number($('shade-slider').value));
document.querySelectorAll('[data-shade]').forEach(button => button.onclick = () => setShade(Number(button.dataset.shade)));
$('shape-mode').onchange = () => { editor.settings.mode = $('shape-mode').value; };
for(const name of ['undo','redo','clear']) $(name).onclick = () => editor.action(name);
window.addEventListener('keydown', e => {
  if (e.target.closest('input,textarea,select,[contenteditable=true]')) return;
  if ((e.ctrlKey || e.metaKey) && ['z','y'].includes(e.key.toLowerCase())) {
    e.preventDefault(); editor.action(e.shiftKey || e.key.toLowerCase()==='y' ? 'redo' : 'undo');
  }
});
$('resolution').onchange = changed;
$('draft-tab').onclick = () => { mode='draft'; render(); };
$('frozen-tab').onclick = () => { mode='frozen'; render(); };
$('snapshots').onchange = () => selectSnapshot($('snapshots').value);
$('refresh-snapshots').onclick = async () => {
  try { await refreshSnapshots(); if ($('snapshots').value) await selectSnapshot($('snapshots').value); } catch(error) { report(error.message); }
};
$('retry').onclick = preview; $('grid').onchange = overlay;
setShade(255); controls(); preview(); refreshSnapshots().catch(error => report(`Snapshot list unavailable: ${error.message}`));
