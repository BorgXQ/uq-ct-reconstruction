"""Finite-step linear actions, independent of spectral covariance calculation."""
import numpy as np


def apply_g(a, x, omega):
    return x - omega * (a.T @ (a @ x))


def apply_t(a, y, k, omega):
    """Raw zero-initialized Landweber, for a vector or a batch of columns."""
    x = np.zeros((a.shape[1],) + y.shape[1:])
    for _ in range(k):
        x += omega * (a.T @ (y - a @ x))
    return x


def apply_t_transpose(a, x, k, omega):
    total = np.zeros_like(x, dtype=float)
    term = x.copy()
    for _ in range(k):
        total += term
        term = apply_g(a, term, omega)
    return omega * (a @ total)


def area_downsample(image, n):
    """Exact overlap averaging, including noninteger scale factors (e.g. 256/24)."""
    size = image.shape[0]
    edges = np.arange(n + 1) * size / n
    left = np.maximum(edges[:-1, None], np.arange(size)[None, :])
    right = np.minimum(edges[1:, None], np.arange(1, size + 1)[None, :])
    weights = np.maximum(0, right - left) / (size / n)
    return weights @ image @ weights.T
