"""Small-resolution UQ experiment orchestration and input validation."""
from functools import lru_cache
import numpy as np
from scipy.stats import norm
from tomography import normalize_image
from .operators import apply_t
from .covariance import geometry, covariance_diagonal, finite_filter
from .bias import source_check, spectral_bound, global_bound, stopping_curve
from .confidence import eligibility, marginal, joint_region
from .monte_carlo import validate


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} to {high}.')
    return value


def number(value, name, low, high):
    if type(value) not in (int, float) or not np.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite and between {low} and {high}.')
    return float(value)


def settings(body):
    image = normalize_image(body.get('image'))
    n = image.shape[0]
    if 'uq_resolution' in body and body['uq_resolution'] != n:
        raise ValueError('Uncertainty must use the same image grid as the reconstruction.')
    if n not in (16, 24):
        raise ValueError('Exact UQ supports only 16 × 16 or 24 × 24 grids.')
    config = dict(n=n, angles=integer(body.get('angles', 90), 'Angles', 1, 180),
                  k=integer(body.get('iterations', 100), 'Iterations', 0, 1000),
                  sigma=number(body.get('noise_std', body.get('sigma', 0)), 'Noise σ', 0, 1),
                  seed=integer(body.get('noise_seed', body.get('seed', 0)), 'Noise seed', 0, 2**32-1),
                  nu=number(body.get('nu', .5), 'Source ν', .01, 4),
                  m=number(body.get('M', 10), 'Source M', 1e-12, 1e12),
                  level=number(body.get('level', .95), 'Confidence level', .9, .99),
                  c0=number(body.get('c0', 1), 'Stopping c₀', .01, 100),
                  budget=integer(body.get('budget', 300), 'Stopping budget', 1, 1000))
    if 0 < config['sigma'] < 1e-12:
        raise ValueError('For reliable UQ arithmetic, use σ = 0 or σ ≥ 1e-12 line-integral units.')
    for key, allowed, default in [('mode', ('variance','source','oracle'), 'variance'),
                                  ('bias_bound', ('spectral','global'), 'spectral'),
                                  ('stopping_mode', ('manual','automatic'), 'manual'),
                                  ('step_mode', ('paper','phase1'), 'paper'),
                                  ('k_policy', ('fixed','exploratory'), 'fixed')]:
        value = body.get(key, default)
        if value not in allowed:
            raise ValueError(f'Invalid {key}.')
        config[key] = value
    return image.ravel(), config


@lru_cache(maxsize=8)
def observation(n, angles, truth_bytes, sigma, seed):
    a = geometry(n, angles)[0]
    truth = np.frombuffer(truth_bytes, dtype=np.float64)
    clean = a @ truth
    noise = np.random.default_rng(seed).normal(0, sigma, clean.shape)
    return clean, noise, clean + noise


@lru_cache(maxsize=8)
def reconstruction(n, angles, truth_bytes, sigma, seed, k, step_mode):
    a, _, _, phase1_step, values, vectors, _ = geometry(n, angles)
    truth = np.frombuffer(truth_bytes, dtype=np.float64)
    clean, noise, observed = observation(n, angles, truth_bytes, sigma, seed)
    omega = 1/(2*values[-1]) if step_mode == 'paper' else phase1_step
    x = apply_t(a, observed, k, omega)
    clean_x = apply_t(a, clean, k, omega)
    mean = vectors @ ((values*finite_filter(values, omega, k)) * (vectors.T @ truth))
    variance, eta = covariance_diagonal(values, vectors, omega, k, sigma)
    return omega, x, clean_x, mean, variance, eta


@lru_cache(maxsize=8)
def stopping(n, angles, truth_bytes, sigma, nu, m, c0, budget, bound_kind):
    _, _, _, _, values, vectors, _ = geometry(n, angles)
    return stopping_curve(values, vectors, 1/(2*values[-1]), sigma, nu, m, c0,
                          budget, np.frombuffer(truth_bytes, dtype=np.float64), bound_kind)


def experiment(body, run_mc=False, select_stopping=False):
    truth, c = settings(body)
    n, angles, k = c['n'], c['angles'], c['k']
    a, theta, offsets, _, values, vectors, tolerance = geometry(n, angles)
    key = truth.tobytes()
    clean, noise, observed = observation(n, angles, key, c['sigma'], c['seed'])
    source = source_check(values, vectors, tolerance, truth, c['nu'], c['m'], c['mode'])
    rank = int(np.count_nonzero(values > tolerance))
    reason = eligibility(source, c['sigma'], c['step_mode'], c['k_policy'])
    search_budget = c['k'] if select_stopping else c['budget']
    if select_stopping:
        c['budget'] = search_budget
    curve = stopping(n, angles, key, c['sigma'], c['nu'], source['M'], c['c0'], search_budget, c['bias_bound']) if reason is None and search_budget > 0 else None
    selection = None
    if select_stopping:
        requested = k
        if c['stopping_mode'] == 'automatic':
            k = curve['first'] if curve and curve['first'] else requested
            message = (f"Automatic stopping: first crossing at K = {k}." if curve and curve['first']
                       else f"Automatic stopping: no crossing within {requested} iterations; used the iteration limit." if curve
                       else f"Automatic stopping unavailable: {reason} Used the iteration limit {requested}.")
        else:
            message = f"Manual stopping: used K = {k}."
        c['k'] = k
        selection = dict(mode=c['stopping_mode'], requested=requested, applied=k,
                         reached=bool(curve and curve['first']), message=message)
    omega, x, clean_x, mean, variance, eta = reconstruction(n, angles, key, c['sigma'], c['seed'], k, c['step_mode'])
    sd = np.sqrt(variance)
    if k == 0:
        reason = 'K = 0 is a constant zero estimate: SD is zero; confidence procedures are disabled.'
        curve = None
    bias = mean-truth
    candidate = c['mode'] != 'variance' and np.isfinite(source['M'])
    spec = spectral_bound(values, omega, k, c['nu'], source['M']) if candidate else None
    global_b = float(global_bound(values[-1], k, c['nu'], source['M'])) if k > 0 and candidate and c['step_mode']=='paper' else None
    bound = spec if c['bias_bound'] == 'spectral' else global_b
    stochastic = norm.ppf((1+c['level'])/2)*sd
    radius = lower = upper = None
    if k > 0 and c['sigma'] > 0 and bound is not None and np.isfinite(bound):
        stochastic, radius, lower, upper = marginal(x, sd, bound, c['level'])
    interval_status = ('conditional' if reason is None else 'exploratory') if lower is not None else 'unavailable'
    joint = joint_region(eta, vectors, error := x-truth, bound, c['level'], rank, reason)
    def square(v):
        return None if v is None else np.asarray(v).reshape(n,n).tolist()
    def sinogram(v):
        return v.reshape(angles, len(offsets)).T.tolist()
    result = dict(config=c, method='Exact small-system covariance (full eigendecomposition, floating point)',
                  omega=float(omega), rank=rank, pixels=n*n, rank_tolerance=tolerance,
                  normal_condition=float(values[-1]/values[0]) if rank==n*n else None,
                  source=source, interval_reason=reason, interval_status=interval_status, selection=selection, joint=joint, stopping=curve,
                  bounds=dict(spectral=spec, global_bound=global_b, selected=bound, kind=c['bias_bound']),
                  measurements=dict(clean=sinogram(clean), noise=sinogram(noise), observed=sinogram(observed),
                                    angles=(theta*180/np.pi).tolist(), offsets=offsets.tolist()),
                  maps={name:square(v) for name,v in dict(truth=truth, reconstruction=x, clean_reconstruction=clean_x,
                       mean=mean, bias=bias, error=error, propagated_noise=x-clean_x, variance=variance, sd=sd,
                       stochastic=stochastic, half_width=radius, lower=lower, upper=upper).items()},
                  diagnostics=dict(bias_norm=float(np.linalg.norm(bias)), error_norm=float(np.linalg.norm(error)),
                                   mean_identity_error=float(np.linalg.norm(mean-clean_x)),
                                   residual=float(np.linalg.norm(a@x-observed)), trace_covariance=float(variance.sum())), mc=None)
    if run_mc:
        trials = integer(body.get('trials', 100), 'Monte Carlo trials', 20, 500)
        mc_seed = integer(body.get('mc_seed', 1), 'Monte Carlo seed', 0, 2**32-1)
        mc = validate(a, values, vectors, truth, mean, variance, omega, k, c['sigma'],
                      trials, mc_seed, radius, joint, eta)
        for name in ('empirical_mean','empirical_variance','empirical_bias'):
            mc[name] = square(mc[name])
        if mc['coverage']:
            for name in ('pixel','pixel_se'):
                mc['coverage'][name] = square(mc['coverage'][name])
        result['mc'] = mc
    return result
