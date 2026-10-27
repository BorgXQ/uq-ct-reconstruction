// Parameter explanations are presentation only; no settings or claims are changed.
const help={
  resolution:'Number of pixels per side in the numerical image. Larger grids preserve more detail but increase reconstruction and uncertainty work.',
  snapshots:'Choose an immutable frozen image. Later drawing edits do not change it.',
  grid:'Show numerical pixel boundaries in the preview; this does not alter the image.',
  'measure-snapshot':'The frozen numerical image used as truth to generate this scan.',
  'n-angles':'Number of equally spaced projection directions over [0°, 180°). More directions give more measurements.',
  'n-detectors':'Number of detector samples per angle. Defaults use MATLAB-style unit-spaced bins; other counts resample that native profile.',
  sigma:'Measurement-noise standard deviation in pixel-unit projection values: y = Ax + ε, ε ~ N(0, σ²I). Set 0 for noiseless measurements.',
  'noise-seed':'Reproduces the Gaussian measurement-noise realization. Changing it changes the noise, not the frozen image or projector.',
  acquisitions:'Choose a saved geometry and its clean projections.',
  'measure-observation':'Choose a saved noisy sinogram from this acquisition.',
  'rq-observation':'The measured sinogram to reconstruct. Its image grid determines the initial retained rank.',
  'rq-trials':'Number of independent measurement-noise trials used to estimate coverage. Uses this reconstruction’s selected K, factors and uncertainty settings; defaults to 100.',
  'rq-mode':'B̃ₖ ≤ vₖ stops at the first K where the retained bias approximation is no larger than the maximum corrected pixel standard deviation (balance = 1). NONE runs a fixed K. An unmet condition does not silently use the maximum as a reconstruction.',
  'rq-budget':'Largest K searched for B̃ₖ ≤ vₖ. Default 1500; the search may stop earlier. This is a limit, not a target iteration count.',
  'rq-k':'Number of Landweber iterations when stopping mode is NONE. Default 500. More iterations can recover detail but amplify measurement noise.',
  'rq-nu':'Source exponent in x† = (AᵀA)ᵛw. Larger ν imposes a stronger restriction on truth in poorly measured directions; it is an assumption, not automatically verified.',
  'calibrate-source':'Automatically calculate M from the frozen drawing and acquisition for the selected exponent ν. This full-spectrum calculation can be expensive. Uncheck to skip it and enter a manual M in Reconstruction.',
  'rq-M':'Optional nonnegative source bound in x = (AᵀA)^ν w, ‖w‖ ≤ M. A filled value always overrides calibration. Leave blank to use a compatible calibrated M. A manual value is an assumption, not proof of compatibility.',
  'rq-calibrated-M':'Numerically calibrated source bound for this observation and exponent ν. Used only when manual M is empty. Calibration does not establish the retained- or omitted-bias assumptions.',
  'rq-alpha':'Gaussian tail probability. α = 0.05 gives a nominal 95% interval; α = 0.01 gives 99%. Smaller α widens the Gaussian allowance. Coverage still depends on the stated assumptions.',
  'rq-rank':'Number r of retained normal-operator directions for uncertainty approximation. Defaults: 32 at 16², 64 at 32², 128 at 64². Landweber still uses the full operator.',
  'rq-p':'Additional random sketch directions beyond r. The sketch width is r + p; more oversampling can improve the approximation but costs work.',
  'rq-count':'Number N of Rademacher probes used to estimate the omitted covariance diagonal. More probes can reduce estimation noise; these are not repeated measurement trials.',
  'rq-eta':'Calculated after K is selected: actual omitted-bias norm / actual retained-bias norm. Uses the frozen drawing; the retained-bias bound must still be checked.',
  'rq-sketch':'Seed for the Gaussian sketch used in randomized SVD. It changes the approximation, not the observed sinogram.',
  'rq-probe':'Seed for the ±1 Rademacher residual probes. It changes the covariance estimate, not measurement noise.',
  'rq-block':'Number of probe or sketch columns processed together. Controls working memory and execution efficiency, not the requested number of samples.',
  'rq-epsilon':'Relative residual-estimation tolerance ε. The correction uses |estimated residual| / (1 − ε). Zero omits residual inflation, following the paper’s numerical approximation; it does not mean zero estimation error. Larger ε increases the allowance.',
  'rq-delta':'Requested failure probability δ for residual-estimation control. Smaller δ generally requires more probes. Entering δ alone does not establish the probability guarantee.',
  'rq-analyses':'Reload a saved reconstruction and its captured settings. Editing the form does not change saved results.',
  'rq-main-row':'Zero-based horizontal image row shown in the confidence-interval plot; the blue line marks it on the image.',
  'rq-inspect-k':'Inspect a different iteration using the same frozen observation and uncertainty settings. This does not change the originally selected stopping K.',
  'rq-map-choice':'Choose an additional numerical diagnostic map; no computation settings are changed.',
  'rq-row':'Zero-based image row for the pixel inspector.',
  'rq-col':'Zero-based image column for the pixel inspector.',
  'rq-own-budget':'Largest K searched when comparing the exact and randomized stopping criteria.',
  'rq-own-c0':'Balance constant for the exact stopping rule Bₖ ≤ c₀vₖ. Larger c₀ permits stopping at a larger bias-to-noise ratio.',
  'rq-own-bound':'Bias bound used by the exact stopping comparison. Global bounds require their matching step-size policy.',
  'vl-template':'Completed analysis whose source, noise, approximation, and confidence settings are frozen for validation.',
  'vl-protocol':'Fixed algorithm varies only measurement noise. All random sources also regenerate the sketch and probes for each trial.',
  'vl-comparison':'Common K compares methods at the same iteration. Own stopping lets each criterion select K separately.',
  'vl-trials':'Number of independent simulated measurements used to estimate coverage. More trials reduce Monte Carlo sampling uncertainty.',
  'vl-seed':'Root seed for reproducible validation trials, with separate streams for measurement noise, sketches, and probes.',
  'vl-k':'Iteration K used in common-K validation. Match it to the reconstruction you want to evaluate.',
  'vl-budget':'Maximum iteration searched in own-stopping validation. A trial without a crossing is recorded as such.',
  'vl-purpose':'Records whether the study is exploratory or was predeclared; this declaration alone does not verify its assumptions.',
  'vl-scope':'Choose reconstruction moments alone, or also confidence intervals and repeated-trial coverage.',
  'vl-diagnostics':'Choose which trials receive truth-based diagnostics. These checks do not alter inference.',
  'vl-pixels':'Comma-separated flattened pixel indices whose trial-level results should be retained for later inspection.',
  'vl-representatives':'Comma-separated trial indices whose full reconstructions and intervals should be retained for display.',
  'vl-diagnostic-k':'Optional comma-separated K values for a truth-based bias–variance diagnostic curve.',
  'vl-c0':'Exact stopping balance: Bₖ ≤ c₀vₖ. The randomized criterion keeps balance fixed at 1.',
  'vl-mc-alpha':'Tail probability for coverage-estimation error bars (Wilson intervals). This describes uncertainty in estimated coverage, not image density.',
  'vl-reference':'Request a full-spectrum comparison if the acquisition is eligible. Large problems can still report the reference as unavailable.',
  'vl-axis':'Parameter varied across the study grid; all other settings are frozen.',
  'vl-values':'Explicit comma-separated values to test for the selected parameter.',
  'vl-studies':'Choose a saved repeated-trial study.',
  'vl-method':'Method whose reconstructions, confidence intervals, and coverage are displayed.',
  'vl-main-trial':'Select the retained single trial used for the reconstructed image and row intervals; this is not a trial average.',
  'vl-main-row':'Zero-based horizontal row shown in the retained trial’s interval plot.',
  'vl-ellipse-x':'Horizontal semiaxis of the coverage ellipse, relative to the image half-width. Only pixel centers inside the ellipse enter its mean.',
  'vl-ellipse-y':'Vertical semiaxis of the coverage ellipse, relative to the image half-height. This display region does not change the intervals.',
  'vl-map-choice':'Select an additional repeated-trial summary map, such as sample variance or empirical mean.',
  'vl-row':'Zero-based row for repeated-trial pixel diagnostics.',
  'vl-col':'Zero-based column for repeated-trial pixel diagnostics.',
  'vl-ledger-offset':'Starting record when loading the next page of trial results.',
  'vl-representative':'Retained trial record to inspect in detail.',
  'vl-import-file':'Load a previously exported numerical study bundle.',
  'vl-replay-trial-index':'Zero-based trial to reproduce from the imported study’s recorded settings and seeds.',
};
const tooltip=document.createElement('div');tooltip.id='parameter-tooltip';tooltip.className='parameter-tooltip';tooltip.role='tooltip';tooltip.hidden=true;document.body.append(tooltip);
let active=null;
function hide(){active?.removeAttribute('aria-describedby');active=null;tooltip.hidden=true;}
function show(button,text){
  hide();active=button;tooltip.textContent=text;tooltip.hidden=false;button.setAttribute('aria-describedby',tooltip.id);
  const r=button.getBoundingClientRect(),box=tooltip.getBoundingClientRect();
  tooltip.style.left=Math.max(12,Math.min(r.left,innerWidth-box.width-12))+'px';
  tooltip.style.top=(r.bottom+8+box.height<=innerHeight?r.bottom+8:Math.max(12,r.top-box.height-8))+'px';
}
function attach(control,text){
  const label=control.closest('label');if(!label)return;
  const button=document.createElement('button');button.type='button';button.className='parameter-help';button.textContent='?';
  button.setAttribute('aria-label','About '+(control.getAttribute('aria-label')||label.childNodes[0]?.textContent?.trim()||label.textContent.trim()||control.id));
  button.dataset.parameter=control.id||control.value;
  const description=()=>control.dataset.help||text;
  button.onmouseenter=()=>show(button,description());button.onmouseleave=()=>{if(document.activeElement!==button)hide();};
  button.onfocus=()=>show(button,description());button.onblur=hide;
  button.onclick=e=>{e.preventDefault();e.stopPropagation();show(button,description());};
  label.insertBefore(button,control);
}
for(const [id,text]of Object.entries(help)){const control=document.getElementById(id);if(control)attach(control,text);}
for(const control of document.querySelectorAll('[name="vl-method"]'))attach(control,{
  corrected:'Intervals with randomized retained covariance, residual correction, and declared bias allowance.',
  retained:'Retained-only diagnostic intervals omit tail contributions and are not full-error guarantees.',
  exact:'Full-spectrum covariance and source-conditioned bias reference, where eligible.',
  tsvd:'Exact truncated-spectrum diagnostic with explicitly accounted omitted contributions.',
}[control.value]);
document.addEventListener('keydown',e=>{if(e.key==='Escape')hide();});
document.addEventListener('pointerdown',e=>{if(active&&!active.contains(e.target))hide();});
window.addEventListener('scroll',hide,true);window.addEventListener('resize',hide);
