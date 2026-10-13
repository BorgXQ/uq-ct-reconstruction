"""Full small-system eigendecomposition; no truncation or randomized SVD."""
from functools import lru_cache
import numpy as np
from scipy.linalg import eigh
from tomography import build_operator

RANK_RTOL = 1e-10
JOINT_MAX_CONDITION = 1e8


def spectrum(a):
    b = a.T @ a
    b = b.toarray() if hasattr(b, 'toarray') else b
    values, vectors = eigh(b)
    tolerance = max(float(values[-1]), np.finfo(float).tiny) * RANK_RTOL
    if values[0] < -tolerance:
        raise ValueError('Normal operator is not numerically positive semidefinite.')
    # Only negative roundoff is clipped; small positive eigenvalues remain in filters.
    values = np.maximum(values, 0)
    return values, vectors, tolerance


@lru_cache(maxsize=4)
def geometry(n, angles):
    if n not in (16, 24):
        raise ValueError('Exact UQ supports only 16 × 16 or 24 × 24 grids.')
    operator, theta, offsets, step = build_operator(n, angles)
    a = operator.sparse
    values, vectors, tolerance = spectrum(a)
    return a, theta, offsets, step, values, vectors, tolerance


def finite_filter(values, omega, k):
    """ω sum_j (1-ωλ)^j, stably evaluated, with exact zero limit ωK.

    Supports the full stable Landweber step range, including negative G modes.
    """
    values = np.asarray(values)
    t = omega * values
    gain = np.empty_like(values, dtype=float)
    positive_g = t < 1
    with np.errstate(divide='ignore'):
        gain[positive_g] = -np.expm1(k * np.log1p(-t[positive_g]))
    gain[~positive_g] = 1 - (1 - t[~positive_g]) ** k
    return np.divide(gain, values, out=np.full_like(gain, omega * k), where=values > 0)


def covariance_eigenvalues(values, omega, k, sigma):
    return sigma**2 * values * finite_filter(values, omega, k)**2


def covariance_diagonal(values, vectors, omega, k, sigma):
    eta = covariance_eigenvalues(values, omega, k, sigma)
    return (vectors * vectors) @ eta, eta
