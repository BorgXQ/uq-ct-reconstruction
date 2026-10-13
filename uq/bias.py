"""Conditional source checks and paper-compatible bias/stopping calculations."""
import numpy as np
from .covariance import covariance_eigenvalues


def source_check(values, vectors, tolerance, truth, nu, proposed_m, mode):
    coeff = vectors.T @ truth
    positive = values > tolerance
    null_norm = float(np.linalg.norm(coeff[~positive]))
    truth_tolerance = 1e-8 * max(1., float(np.linalg.norm(truth)))
    w_norm = float(np.linalg.norm(coeff[positive] / values[positive]**nu))
    m = max(w_norm * (1 + 1e-8), 1e-12) if mode == 'oracle' else proposed_m
    status, reason = 'verified', 'Source condition passes the synthetic numerical check; coverage remains conditional on the exact assumption.'
    if mode == 'variance':
        status, reason = 'missing', 'Variance-only mode: no source condition asserted; no bias-adjusted coverage claim.'
    elif null_norm > truth_tolerance:
        status, reason = 'failed', 'Ground truth has a component in the numerical null space; the source condition fails.'
    elif np.any((values > tolerance / 10) & (values < tolerance * 10)):
        status, reason = 'unverified', 'Eigenvalues near the numerical rank threshold make source compatibility unreliable.'
    elif not np.isfinite(w_norm) or not np.isfinite(m):
        status, reason = 'unverified', 'Source calculation is too ill-conditioned to verify.'
    elif m < w_norm:
        status, reason = 'failed', 'Proposed M is smaller than the minimum-norm source candidate.'
    return dict(status=status, reason=reason, mode=mode, nu=nu, M=float(m),
                w_norm=w_norm, null_norm=null_norm, truth_tolerance=truth_tolerance,
                rank_tolerance=tolerance, oracle=mode == 'oracle')


def spectral_bound(values, omega, k, nu, m):
    return float(m * np.max(np.abs(1 - omega * values)**k * values**nu))


def global_bound(largest, k, nu, m):
    """Eq. 3.18, ONLY for ω=1/(2 λmax); use log space for large K."""
    k = np.asarray(k, dtype=float)
    return np.exp(nu*np.log(2*largest) + np.log(m) + nu*np.log(nu)
                  + k*np.log(k) - (nu+k)*np.log(nu+k))


def stopping_curve(values, vectors, omega, sigma, nu, m, c0, budget, truth, bound_kind="spectral"):
    ks = np.arange(1, budget + 1)
    etas = np.column_stack([covariance_eigenvalues(values, omega, int(k), sigma) for k in ks])
    max_sd = np.sqrt(np.max((vectors*vectors) @ etas, axis=0))
    bounds = (np.array([spectral_bound(values, omega, int(k), nu, m) for k in ks])
              if bound_kind == "spectral" else global_bound(values[-1], ks, nu, m))
    coeff = vectors.T @ truth
    actual_bias = np.linalg.norm((1-omega*values[:, None])**ks * coeff[:, None], axis=0)
    crossings = np.flatnonzero(bounds <= c0 * max_sd)
    first = int(ks[crossings[0]]) if crossings.size else None
    return dict(iterations=ks.tolist(), bound=bounds.tolist(), max_sd=max_sd.tolist(),
                oracle_bias=actual_bias.tolist(), c0=c0, first=first)
