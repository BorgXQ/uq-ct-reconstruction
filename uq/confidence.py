"""Eligibility gates and raw marginal/joint confidence calculations."""
import numpy as np
from scipy.stats import norm, chi2
from .covariance import JOINT_MAX_CONDITION


def eligibility(source, sigma, step_mode, k_policy):
    if source['status'] != 'verified':
        return source['reason']
    if sigma <= 0:
        return 'σ = 0: deterministic bias diagnostics only; Gaussian confidence procedures are disabled.'
    if step_mode != 'paper':
        return 'Paper confidence and stopping formulas require ω = 1/(2 λmax). The spectral bias bound remains available.'
    if k_policy != 'fixed':
        return 'K selected after inspecting noisy data is exploratory; fixed-K coverage claims are disabled.'
    return None


def marginal(reconstruction, sd, allowance, level):
    stochastic = norm.ppf((1 + level) / 2) * sd
    radius = stochastic + allowance
    return stochastic, radius, reconstruction-radius, reconstruction+radius


def joint_region(eta, vectors, error, bound, level, rank, disabled_reason=None):
    if disabled_reason:
        return dict(available=False, reason=disabled_reason)
    if rank != eta.size or np.min(eta) <= 0:
        return dict(available=False, reason='Rank-deficient covariance: the inverse-covariance region is unavailable. No pseudoinverse is substituted.')
    condition = float(np.max(eta)/np.min(eta))
    if condition > JOINT_MAX_CONDITION:
        return dict(available=False, reason=f'Covariance condition number exceeds {JOINT_MAX_CONDITION:g}; joint region is numerically unreliable.', condition=condition)
    # Orthogonal spectral solve, never form the inverse or a pseudoinverse.
    statistic = float(np.sum((vectors.T @ error)**2 / eta))
    with np.errstate(over='ignore', divide='ignore', invalid='ignore'):
        delta = bound / np.sqrt(np.min(eta))
        threshold = float((np.sqrt(chi2.ppf(level, eta.size)) + delta)**2)
    if not np.isfinite(statistic) or not np.isfinite(threshold):
        return dict(available=False, reason='Joint-region arithmetic exceeds floating-point range; increase the noise scale.')
    return dict(available=True, statistic=statistic, threshold=threshold,
                contains_truth=statistic <= threshold, condition=condition, delta=float(delta))
