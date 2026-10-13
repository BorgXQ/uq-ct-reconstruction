"""Notebook-compatible Radon measurements and zero-initialized Landweber."""
from functools import lru_cache
import numpy as np
from radon_operator import RadonOperator, notebook_step


def normalize_image(values):
    """Validate an already area-downsampled density image, without rescaling it."""
    image = np.asarray(values, dtype=np.float64)
    if image.shape not in ((16, 16), (24, 24)):
        raise ValueError("Image must be 16 × 16 or 24 × 24.")
    if not np.isfinite(image).all() or np.any((image < 0) | (image > 1)):
        raise ValueError("Image densities must be finite and between 0 and 1.")
    return image


@lru_cache(maxsize=4)
def build_operator(n, angle_count):
    """Notebook Radon geometry, matched transpose, and power-iteration step."""
    if n not in (16, 24, 64, 128, 256) or not 1 <= angle_count <= 180:
        raise ValueError("Unsupported geometry.")
    operator = RadonOperator(n, angle_count)
    offsets = np.arange(operator.detectors) - operator.detectors//2
    return operator, operator.radians, offsets, notebook_step(operator)


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
    transpose = matrix.T
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
