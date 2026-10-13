"""Small-resolution UQ experiment orchestration and input validation."""
from functools import lru_cache
import numpy as np
from scipy.stats import norm
from tomography import normalize_image, build_operator, project
from .operators import apply_t
from .covariance import geometry, covariance_diagonal, finite_filter
from .bias import source_check, spectral_bound, global_bound, stopping_curve
from .confidence import eligibility, marginal, joint_region
from .monte_carlo import validate
from .practical import Practical


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
                  k=integer(body.get('iterations', 100), 'Iterations', 0, 2**53-1),
                  sigma=number(body.get('noise_std', body.get('sigma', 0)), 'Noise σ', 0, 1),
                  seed=integer(body.get('noise_seed', body.get('seed', 0)), 'Noise seed', 0, 2**32-1),
                  nu=number(body.get('nu', .5), 'Source ν', .01, 4),
                  m=number(body.get('M', 10), 'Source M', 1e-12, 1e12),
                  level=number(body.get('level', .95), 'Confidence level', .9, .99),
                  c0=number(body.get('c0', 1), 'Stopping c₀', .01, 100),
                  budget=integer(body.get('budget', 300), 'Stopping budget', 1, 2**53-1),
                  retained_rank=integer(body.get('retained_rank', 64), 'Retained rank', 1, n*n),
                  oversampling=integer(body.get('oversampling', 10), 'Oversampling', 4, n*n),
                  probes=integer(body.get('probes', 100), 'Residual probes', 1, 10000),
                  sketch_seed=integer(body.get('sketch_seed', 17), 'Sketch seed', 0, 2**32-1),
                  probe_seed=integer(body.get('probe_seed', 23), 'Probe seed', 0, 2**32-1),
                  probe_epsilon=number(body.get('probe_epsilon', .1), 'Residual tolerance ε', .001, .99),
                  probe_delta=number(body.get('probe_delta', .01), 'Probe failure probability δ', .000001, .5),
                  tail_eta=number(body.get('tail_eta', .1), 'Omitted bias ratio η', 0, 1))
    if 0 < config['sigma'] < 1e-12:
        raise ValueError('For reliable UQ arithmetic, use σ = 0 or σ ≥ 1e-12 projection units.')
    for key, allowed, default in [('mode', ('variance','source','oracle'), 'variance'),
                                  ('uq_method', ('practical','classical'), 'practical'),
                                  ('bias_bound', ('spectral','global'), 'spectral'),
                                  ('stopping_mode', ('manual','automatic'), 'manual'),
                                  ('step_mode', ('paper','phase1'), 'paper'),
                                  ('k_policy', ('fixed','exploratory'), 'fixed')]:
        value = body.get(key, default)
        if value not in allowed:
            raise ValueError(f'Invalid {key}.')
        config[key] = value
    # Legacy clients use the notebook step too; there is now one operator/step.
    config['step_mode'] = 'paper'
    return image.ravel(), config


@lru_cache(maxsize=8)
def observation(n, angles, truth_bytes, sigma, seed):
    truth = np.frombuffer(truth_bytes, dtype=np.float64).reshape(n, n)
    operator, observed, _, _, _ = project(truth, angles, sigma, seed)
    clean = operator @ truth.ravel()
    noise = sigma*np.random.default_rng(seed).standard_normal(clean.shape)
    return clean, noise, observed


@lru_cache(maxsize=8)
def reconstruction(n, angles, truth_bytes, sigma, seed, k, step_mode):
    a, _, _, omega, values, vectors, _ = geometry(n, angles)
    truth = np.frombuffer(truth_bytes, dtype=np.float64)
    clean, noise, observed = observation(n, angles, truth_bytes, sigma, seed)
    if k > 1000:
        gain = finite_filter(values, omega, k)
        x = vectors @ (gain * (vectors.T @ (a.T @ observed)))
        clean_x = vectors @ ((values*gain) * (vectors.T @ truth))
    else:
        operator = build_operator(n, angles)[0]
        x = apply_t(operator, observed, k, omega)
        clean_x = apply_t(operator, clean, k, omega)
    mean = vectors @ ((values*finite_filter(values, omega, k)) * (vectors.T @ truth))
    variance, eta = covariance_diagonal(values, vectors, omega, k, sigma)
    return omega, x, clean_x, mean, variance, eta


@lru_cache(maxsize=8)
def stopping(n, angles, truth_bytes, sigma, nu, m, c0, budget, bound_kind):
    _, _, _, omega, values, vectors, _ = geometry(n, angles)
    return stopping_curve(values, vectors, omega, sigma, nu, m, c0,
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
    practical = Practical(c, truth, source) if c['uq_method']=='practical' else None
    curve = None
    if reason is None and search_budget > 0:
        curve = (practical.stopping(search_budget) if practical else
                 stopping(n, angles, key, c['sigma'], c['nu'], source['M'], c['c0'], search_budget, c['bias_bound']))
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
    global_b = float(global_bound(1/(2*omega), k, c['nu'], source['M'])) if k > 0 and candidate and c['step_mode']=='paper' else None
    bound = spec if c['bias_bound'] == 'spectral' else global_b
    interval_sd = sd
    practical_audit = approximate_variance = residual_estimate = corrected_variance = None
    if practical:
        practical_audit, approximate_variance, residual_estimate, corrected_variance = practical.audit(k, variance)
        interval_sd = np.sqrt(corrected_variance)
        bound = practical_audit['allowance'] if candidate else None
        if practical_audit['issues']:
            reason = ' '.join(([reason] if reason else []) + practical_audit['issues'])
    stochastic = norm.ppf((1+c['level'])/2)*interval_sd
    radius = lower = upper = None
    if k > 0 and c['sigma'] > 0 and bound is not None and np.isfinite(bound):
        stochastic, radius, lower, upper = marginal(x, interval_sd, bound, c['level'])
    interval_status = ('conditional' if reason is None else 'exploratory') if lower is not None else 'unavailable'
    joint = joint_region(eta, vectors, error := x-truth, bound, c['level'], rank, reason)
    def square(v):
        return None if v is None else np.asarray(v).reshape(n,n).tolist()
    def sinogram(v):
        return v.reshape(len(offsets), angles).tolist()
    result = dict(config=c, method=('Paper §3.6 randomized UQ with exact small-grid audits' if practical else 'Exact small-system covariance (full eigendecomposition, floating point)'), practical=practical_audit,
                  omega=float(omega), rank=rank, pixels=n*n, rank_tolerance=tolerance,
                  normal_condition=float(values[-1]/values[0]) if rank==n*n else None,
                  source=source, interval_reason=reason, interval_status=interval_status, selection=selection, joint=joint, stopping=curve,
                  bounds=dict(spectral=spec, global_bound=global_b, selected=bound, kind='practical' if practical else c['bias_bound'], stopping=practical_audit['retained_bound'] if practical else bound),
                  measurements=dict(clean=sinogram(clean), noise=sinogram(noise), observed=sinogram(observed),
                                    angles=(theta*180/np.pi).tolist(), offsets=offsets.tolist()),
                  maps={name:square(v) for name,v in dict(truth=truth, reconstruction=x, clean_reconstruction=clean_x,
                       mean=mean, bias=bias, error=error, propagated_noise=x-clean_x, variance=variance, sd=sd,
                       interval_sd=interval_sd, approximate_variance=approximate_variance, residual_estimate=residual_estimate, corrected_variance=corrected_variance, stochastic=stochastic, half_width=radius, lower=lower, upper=upper).items()},
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


def spectral_history(n, angles, y, truth, iterations, omega):
    """Evaluate the same untruncated Landweber iterates at 101 saved indices."""
    a, _, _, _, values, vectors, _ = geometry(n, angles)
    coefficients = vectors.T @ (a.T @ y)
    truth_norm = np.linalg.norm(truth)
    history, snapshots = [], []
    for k in sorted({i*iterations//100 for i in range(101)}):
        x = vectors @ (finite_filter(values, omega, k)*coefficients)
        history.append(dict(iteration=k, residual=float(np.linalg.norm(y-a@x)),
                            error=float(np.linalg.norm(x-truth)/truth_norm) if truth_norm else None))
        snapshots.append(dict(iteration=k, image=x.reshape(n,n).tolist()))
    return dict(history=history, snapshots=snapshots, step=omega)
