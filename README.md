# CT reconstruction laboratory

A local mathematical demonstration for phase one of **Can We Trust This CT Reconstruction?** Draw a scalar density field, generate parallel-beam measurements with optional Gaussian noise, and reconstruct it with Landweber iteration. Python owns the numerical implementation; Flask serves a plain JavaScript canvas editor. No accounts, external APIs, or services are used at runtime.

## Run

Python 3.10 or newer:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Stop with Ctrl+C. The server binds only to localhost. The built-in server is appropriate for this local demonstration; deployment is outside this phase.

## Explore

1. Draw on the black canvas. Choose any grayscale shade from 0 to 255 and a brush width in editor pixels. Eraser paints zero. Circle drag starts at its center; square drag starts at a corner and uses the larger drag distance for both sides. Shapes may be clipped by the canvas edge.
2. Fill replaces a **four-connected** region, with a fixed tolerance of 8 grayscale levels from the clicked seed. This accommodates slight antialiasing while stopping at different-density boundaries; it never globally replaces disconnected regions. A gap in a boundary permits a fill to escape, as in a conventional paint editor.
3. Choose a 64², 128², or 256² grid and 1–180 projection angles. Set Gaussian noise σ from 0 to 1 in absolute line-integral units (for example, 0.01 for mild noise), then generate projections. Zero disables noise; the seed repeats the noise realization. Changing the seed produces another realization. The original canvas remains unchanged.
4. Choose 1–1000 iterations and run Landweber. Use the saved-iteration slider to inspect up to 101 (64²), 64 (128²), or 16 (256²) evenly spaced iterates, including zero and the final iterate, without recomputation. Diagnostics are retained at every iteration.
5. Editing the drawing or projection or noise settings dims the old results and disables reconstruction and the slider until new projections are generated. Changing the iteration count keeps the previous run available and asks for a new reconstruction.

The three images share orientation. Original and reconstruction use the fixed density scale [0, 1]. Only reconstruction **display** clips values outside that range; the solver and errors use the unmodified, unconstrained iterates. The sinogram uses its own explicitly reported [min(0, minimum measurement), max(0, maximum measurement)] grayscale range. Its horizontal axis is angle; its vertical axis is signed detector offset, increasing downward.

## Numerical model

The image occupies the square [-1, 1] × [-1, 1], with pixel side length h = 2/N. Array rows run downward and columns run rightward; positive mathematical y therefore points down in this implementation. The 512² editor is exported by exact block area averaging of grayscale values, followed by division by 255. No thresholding or per-image normalization occurs.

For a normal n(θ) = (cos θ, sin θ), the continuous Radon measurement is

```text
R f(θ, s) = ∫ f(s n(θ) + t (-sin θ, cos θ)) dt.
```

We model f as constant inside each mathematical pixel. Intersecting the ray with every vertical and horizontal grid boundary divides it into segments; each segment's midpoint identifies its pixel and its Euclidean length supplies that pixel's coefficient. Thus A[r, j] is the ray's path length inside pixel j and the noiseless measurements are Ax. Optional independent Gaussian noise gives y = Ax + ε, with εᵢ ∼ N(0, σ²). Negative noisy measurements are retained, both for reconstruction and the sinogram display. The custom implementation is in `tomography.py`; no ready-made Radon or reconstruction routine is used.

There are M equally spaced angles θ = mπ/M, excluding π. Detectors have spacing h and count ceil(√2 N) + 1, with symmetric offsets about zero, covering the whole square's diagonal. Rays outside the square give zero rows. A ray exactly on an internal pixel boundary uses the positive-side pixel; rays on the square's outer boundary use the adjacent interior pixel. These deterministic conventions concern a measure-zero set of continuous rays. Rows of the CSR sparse matrix are angle-major, and image columns use row-major flattening. Up to two recently used operators are cached in memory.

Starting at x⁽⁰⁾ = 0, the code applies

```text
x⁽ᵏ⁺¹⁾ = x⁽ᵏ⁾ + ω Aᵀ (y − Ax⁽ᵏ⁾).
F(x) = ½ ‖Ax − y‖₂².
```

This is gradient descent on F. Backprojection uses the **actual transpose** of the same sparse matrix. We choose ω = 1/B where B = ‖A‖₁ ‖A‖∞, the product of maximum absolute column and row sums. Since ‖A‖₂² ≤ B, this guarantees 0 < ω < 2/‖A‖₂². This conservative bound is deterministic and avoids relying on an underestimated spectral norm. There is no nonnegativity projection or regularization.

The stateless reconstruction endpoint regenerates y from the saved ground-truth snapshot using the same deterministic geometry, cached operator, noise standard deviation, and seed used for projection. Thus every iteration count uses exactly the same noisy measurements. Larger grids take more time and memory; saved image counts are capped to bound response size, while diagnostics retain every iteration. The UI locks controls during a request and reports calculation activity. The server runs one calculation at a time. No image is persisted.

The residual is ‖Ax − y‖₂. Relative image error is ‖x − x_true‖₂ / ‖x_true‖₂; for an all-zero original it is reported as unavailable, while the residual remains zero only when noise is disabled. The graph normalizes residuals by the initial residual (zero when the initial residual is zero), allowing comparison with relative image error. More iterations lower the least-squares objective with this step size, but sparse angles may leave an underdetermined problem and image error need not generally be monotone. With noise, extra iterations may fit noise and increase image error even as residual decreases. Zero noise does not guarantee exact reconstruction with finite angles and iterations.

## Verification

```sh
python -m unittest discover -s tests -v
node --check static/app.js
```

Tests cover axis-aligned and diagonal ray lengths, orientation, density validation, the numerical adjoint identity at all three grid sizes, objective monotonicity, a 64² asymmetric phantom reconstruction, the safe step-size bound, zero-image behavior, reproducible Gaussian noise, large request sizes, bounded snapshot counts, and HTTP validation/workflow.

An optional browser smoke test uses Node.js 22+ and a locally installed Chromium, with the Flask server already running:

```sh
CHROMIUM_PATH=/path/to/chromium node scripts/check-browser.mjs
```

It checks actual pointer gestures, grayscale preservation, all drawing tools, connected fill, projection/reconstruction, saved iterations, stale results, a black input, and mobile overflow. It saves a desktop screenshot to `/tmp/ct-laboratory-desktop.png`. Chromium runs headlessly with an isolated temporary profile; it is not a runtime dependency.

For an interactive check, draw a gray closed brush loop, fill inside, and confirm the disconnected background is unchanged. Try all shape tools, erase and clear. Reconstruct an asymmetric image to check orientation, scrub saved iterates, change angle count to check stale-result handling, and repeat with a completely black canvas.

This phase intentionally excludes uncertainty analysis, inference, randomized SVD, learning models, and clinical interpretation.
