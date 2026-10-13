"""Parallel-beam CT on piecewise-constant pixels in [-1, 1]²."""
from functools import lru_cache
from array import array
import numpy as np
from scipy.sparse import csr_matrix


def normalize_image(values):
    """Validate an already area-downsampled density image, without rescaling it."""
    image = np.asarray(values, dtype=np.float64)
    if image.shape not in ((16, 16), (24, 24)):
        raise ValueError("Image must be 16 × 16 or 24 × 24.")
    if not np.isfinite(image).all() or np.any((image < 0) | (image > 1)):
        raise ValueError("Image densities must be finite and between 0 and 1.")
    return image


@lru_cache(maxsize=2)
def build_operator(n, angle_count):
    """Split each ray at grid crossings; segment lengths are CSR coefficients.

    Coordinates: x increases right, y increases down (array row convention).
    A ray is s*(cos(theta),sin(theta)) + t*(-sin(theta),cos(theta)).
    Rows are angle-major; columns are row-major image pixels.
    """
    if n not in (16, 24, 64, 128, 256) or not 1 <= angle_count <= 180:
        raise ValueError("Unsupported geometry.")
    angles = np.arange(angle_count) * np.pi / angle_count
    spacing = 2.0 / n
    detector_count = int(np.ceil(np.sqrt(2) * n)) + 1
    offsets = (np.arange(detector_count) - (detector_count - 1) / 2) * spacing
    edges = np.linspace(-1, 1, n + 1)
    # Packed buffers avoid millions of boxed Python numbers on larger grids.
    indices, data, indptr = array('i'), array('d'), [0]
    for theta in angles:
        normal = np.array([np.cos(theta), np.sin(theta)])
        normal[np.abs(normal) < 1e-12] = 0
        direction = np.array([-normal[1], normal[0]])
        for offset in offsets:
            origin = offset * normal
            lower, upper = -np.inf, np.inf
            crossings = []
            for axis in range(2):
                if abs(direction[axis]) < 1e-12:
                    if not -1 <= origin[axis] <= 1:
                        upper = -np.inf
                        break
                else:
                    ts = (edges - origin[axis]) / direction[axis]
                    lower = max(lower, min(ts[0], ts[-1]))
                    upper = min(upper, max(ts[0], ts[-1]))
                    crossings.extend(ts)
            if upper > lower:
                ts = np.unique(np.clip(crossings, lower, upper))
                lengths = np.diff(ts)
                keep = lengths > 1e-12
                midpoints = origin[:, None] + direction[:, None] * ((ts[:-1] + ts[1:]) / 2)
                pixels = np.clip(np.floor((midpoints + 1) / spacing).astype(int), 0, n - 1)
                indices.frombytes((pixels[1] * n + pixels[0])[keep].astype(np.int32).tobytes())
                data.frombytes(lengths[keep].tobytes())
            indptr.append(len(data))
    matrix = csr_matrix((np.frombuffer(data, dtype=np.float64),
                         np.frombuffer(indices, dtype=np.int32), indptr),
                        shape=(angle_count * detector_count, n * n))
    # ||A||₂² <= ||A||₁ ||A||∞. A rigorous bound avoids unsafe spectral estimates.
    bound = float(np.max(matrix.sum(axis=0)) * np.max(matrix.sum(axis=1)))
    return matrix, angles, offsets, 1.0 / bound


def project(image, angle_count, noise_std=0.0, noise_seed=0):
    """y = Ax + independent N(0, noise_std²), reproducible from the seed."""
    if isinstance(noise_std, bool) or not isinstance(noise_std, (int, float)) or not np.isfinite(noise_std) or not 0 <= noise_std <= 1:
        raise ValueError("Noise standard deviation must be a finite number from 0 to 1.")
    if type(noise_seed) is not int or not 0 <= noise_seed <= 2**32 - 1:
        raise ValueError("Noise seed must be an integer from 0 to 4294967295.")
    matrix, angles, offsets, step = build_operator(image.shape[0], angle_count)
    measurements = matrix @ image.ravel()
    if noise_std:
        measurements += np.random.default_rng(noise_seed).normal(0, noise_std, measurements.shape)
    return matrix, measurements, angles, offsets, step


def landweber(matrix, measurements, truth, iterations, step):
    """Unconstrained descent; bound snapshot storage to 1,048,576 values."""
    transpose = matrix.T.tocsr()
    x = np.zeros(matrix.shape[1])
    norm_truth = np.linalg.norm(truth)
    saved = set(np.linspace(0, iterations, min(iterations + 1, 101, 1048576 // x.size), dtype=int))
    history, snapshots = [], []
    residual = measurements - matrix @ x
    for k in range(iterations + 1):
        history.append({"iteration": k, "residual": float(np.linalg.norm(residual)),
                        "error": float(np.linalg.norm(x - truth) / norm_truth) if norm_truth else None})
        if k in saved:
            snapshots.append({"iteration": k, "image": x.reshape(int(np.sqrt(x.size)), -1).tolist()})
        if k < iterations:
            x += step * (transpose @ residual)
            residual = measurements - matrix @ x
    return {"history": history, "snapshots": snapshots, "step": step}
