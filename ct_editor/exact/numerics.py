"""Pure deterministic UQ kernels. No observed y, clean data, truth, or RNG."""
import math
import numpy as np
from scipy.stats import norm
from threadpoolctl import threadpool_limits
from ..images import InputError
from ..ct.geometry import fields, scalar, integer, identifier
from ..ct.jobs import Canceled
from ..ct.solver import frozen_array, validate_step, power_half_step


def source_config(data):
    if data is None: return None
    fields(data, ('nu','M','provenance'), ('nu','M','provenance'))
    nu = scalar(data['nu'], 'nu')
    if nu <= 0: raise InputError('Source exponent nu must be positive.', 'nu')
    return dict(nu=nu, M=scalar(data['M'],'M'), provenance=identifier(data['provenance'],'provenance'))


def quantile(alpha):
    alpha = scalar(alpha, 'alpha', 0, 1)
    if not 0 < alpha < 1: raise InputError('Require 0 < alpha < 1.', 'alpha')
    z = float(norm.isf(alpha/2))
    if not math.isfinite(z) or z <= 0:
        raise InputError('Normal quantile outside numerical range.', code='numerical_range', status=422)
    return z


def resolve_exact_step(operator, spectrum, policy, inherited=None):
    if spectrum.metadata()['operator_id'] != operator.metadata()['operator_id']:
        raise InputError('Spectrum/operator identity mismatch.', code='incompatible_reconstruction', status=422)
    if inherited is None and policy == 'power_iteration_half_v1':
        result = power_half_step(operator)
        validate_step(operator, result['omega'], spectrum.certificate())
        return result
    maximum = spectrum.metadata()['lambda_max']
    safe = operator.metadata()['L_safe']
    if inherited is not None:
        omega = inherited['omega']; policy = inherited['step_policy']
    elif policy == 'exact_spectral_half': omega = 1/(2*maximum)
    elif policy == 'safe_induced_norm_v1': omega = 1/(2*safe)
    else: raise InputError('Unsupported exact step policy.', 'step_policy')
    validate_step(operator, omega, spectrum.certificate())
    return dict(omega=omega, step_policy=policy, lambda_max=maximum, L_safe=safe,
                stability_basis='full_svd_numerical', stability_tolerance=1e-12,
                stable_certified=True, nonoscillatory_certified=omega*maximum <= 1)


def compatible_bound(method, step):
    required = {'global':'exact_spectral_half','safe_global':'safe_induced_norm_v1'}
    if method not in ('spectral','global','safe_global'): raise InputError('Unsupported bias method.', 'bound_method')
    if method in required and step['step_policy'] != required[method]:
        raise InputError('Selected global bound requires its matching half-step policy; use the spectral bound or create a new run.',
                         'bound_method', 'bound_step_mismatch', 422)


def filters(eigenvalues, omega, k):
    """Returns c=omega sum g^j and a=lambda*c², including negative g and tiny lambda."""
    values = np.asarray(eigenvalues, dtype=float)
    integer(k,'iteration',0,None)
    if np.any(values < 0) or not np.isfinite(values).all(): raise InputError('Invalid eigenvalues.')
    t = omega*values
    if np.any(t >= 2) or omega <= 0: raise InputError('Unstable spectral filter.', code='invalid_step', status=422)
    c = np.full_like(values, k*omega)
    if k == 0: return c, np.zeros_like(values)
    small = (t > 0) & (t < 1)
    # Divide by t (rather than lambda); t=0 has the continuous geometric-sum limit.
    c[small] = omega*(-np.expm1(k*np.log1p(-t[small])))/t[small]
    other = t >= 1
    c[other] = omega*(1-np.power(1-t[other],k))/t[other]
    with np.errstate(over='ignore', under='ignore'):
        a = values*c*c
    if not np.isfinite(a).all(): raise InputError('Covariance filter overflow.',code='numerical_range',status=422)
    return c,a


def covariance(spectrum, omega, k, sigma):
    _, a = filters(spectrum.eigenvalues, omega, k)
    with np.errstate(over='ignore', under='ignore'):
        weights = (sigma*np.sqrt(a))**2
    if not np.isfinite(weights).all(): raise InputError('Covariance outside numerical range.',code='numerical_range',status=422)
    warnings = []
    if sigma > 0 and k > 0 and np.any((spectrum.eigenvalues > 0) & (weights == 0)):
        warnings.append('Positive covariance contribution underflowed; numerical zero is not an exact-zero certificate.')
    with threadpool_limits(limits=1, user_api='blas'):
        diagonal = spectrum.squared_vectors @ weights
    sd = np.sqrt(diagonal)
    return dict(variance=frozen_array(diagonal), pixel_sd=frozen_array(sd), trace=float(np.sum(weights)),
                max_pixel_sd=float(np.max(sd)), weights=weights, warnings=warnings)


def covariance_action(spectrum, omega, k, sigma, vector):
    weights = covariance(spectrum,omega,k,sigma)['weights']
    return spectrum.vectors @ (weights*(spectrum.vectors.T @ vector))


def bias_bound(spectrum, step, k, source, method):
    compatible_bound(method, step)
    if source is None or k == 0: return None
    M,nu = source['M'],source['nu']
    if M>0 and spectrum.metadata()['eigenvalue_underflow_modes']:
        raise InputError('Positive singular values lost their squared scale; source allowance unresolved.',code='numerical_range',status=422)
    log_value = -math.inf
    direct_value = None
    algebraic_zero = M == 0
    if M > 0:
        if method == 'spectral':
            lam = spectrum.eigenvalues
            t = step['omega']*lam
            positive = (lam > 0) & (t != 1)
            algebraic_zero = not np.any(positive)
            if np.any(positive):
                t=t[positive]
                # log1p avoids rounding tiny omega*lambda out of 1-t.
                logs=np.empty_like(t);small=t<1
                logs[small]=np.log1p(-t[small]);logs[~small]=np.log(t[~small]-1)
                log_value=float(math.log(M)+np.max(k*logs+nu*np.log(lam[positive])))
                # Preserve exactly representable simple powers/equality fixtures.
                # Log evaluation remains authoritative if intermediates under/overflow.
                with np.errstate(over='ignore',under='ignore',invalid='ignore'):
                    terms=np.power(np.abs(1-step['omega']*lam[positive]),k)*np.power(lam[positive],nu)*M
                if np.isfinite(terms).all() and np.max(terms)>0:
                    candidate=float(np.max(terms))
                    if abs(math.log(candidate)-log_value)<=1e-12:
                        direct_value=candidate
        else:
            L=step['lambda_max'] if method=='global' else step['L_safe']
            # log ratios avoid powers and large K*log(K) cancellation.
            log_value=(math.log(M)+nu*(math.log(2)+math.log(L))
                       -nu*math.log1p(k/nu)-k*math.log1p(nu/k))
    if math.isnan(log_value) or log_value == math.inf or (log_value == -math.inf and not algebraic_zero):
        raise InputError('Bias bound outside numerical range.',code='numerical_range',status=422)
    exact_zero=algebraic_zero
    value=0.0 if exact_zero else direct_value if direct_value is not None else math.exp(log_value) if log_value <= math.log(np.finfo(float).max) else None
    status='exact_zero' if exact_zero else 'overflow' if value is None else 'underflow' if value==0 else 'finite'
    return dict(value=value, log_value=None if exact_zero else log_value, exact_zero=exact_zero, numerical_status=status,
                method=method, source_status='assumed_unverified')


def first_crossing(spectrum, step, sigma, source, method, c0, budget, check=lambda:None, progress=lambda *args:None):
    if source is None: raise InputError('Automatic stopping requires an explicit source declaration.',code='source_missing',status=422)
    if not step['nonoscillatory_certified']: raise InputError('Automatic route requires a nonoscillatory step.',code='invalid_step',status=422)
    compatible_bound(method,step)
    c0=scalar(c0,'c0');integer(budget,'budget',1,None)
    if c0<=0: raise InputError('c0 must be positive.','c0')
    history=[];c=np.zeros_like(spectrum.eigenvalues);g=1-step['omega']*spectrum.eigenvalues
    with threadpool_limits(limits=1,user_api='blas'):
        for k in range(1,budget+1):
            try: check()
            except Canceled:
                return dict(status='canceled',selected_k=None,history=history,last_evaluated_k=len(history))
            c=g*c+step['omega']
            weights=(sigma*np.sqrt(spectrum.eigenvalues)*c)**2
            if not np.isfinite(weights).all(): raise InputError('Search covariance overflow.',code='numerical_range',status=422)
            if sigma>0 and np.any((spectrum.eigenvalues>0)&(weights==0)):
                raise InputError('Search covariance underflow; crossing unresolved.',code='numerical_range',status=422)
            v=float(np.sqrt(np.max(spectrum.squared_vectors @ weights)))
            b=bias_bound(spectrum,step,k,source,method)
            threshold=c0*v
            if not math.isfinite(threshold): raise InputError('Stopping threshold overflow.',code='numerical_range',status=422)
            # Compare representable values directly so an equality remains a crossing.
            crossed=(b['exact_zero'] or (v>0 and (b['value']<=threshold if b['numerical_status']=='finite'
                       else b['log_value'] <= math.log(c0)+math.log(v))))
            history.append(dict(iteration=k,bias_bound=b['value'],log_bias=b['log_value'],max_pixel_sd=v,
                threshold=threshold,crossed=crossed,bound_method=method,numerical_status=b['numerical_status']))
            progress('Searching bias–noise crossing',k,budget)
            if crossed: return dict(status='criterion_met',selected_k=k,history=history,last_evaluated_k=k)
    return dict(status='criterion_not_met',selected_k=None,history=history,last_evaluated_k=budget)


def intervals(center, cov, bound, alpha):
    z=quantile(alpha);noise=z*cov['pixel_sd']
    data=dict(center=frozen_array(center),variance=cov['variance'],pixel_sd=cov['pixel_sd'],noise_half_width=frozen_array(noise))
    if bound is not None:
        if bound['numerical_status'] not in ('finite','exact_zero'):
            raise InputError('Interval bias allowance cannot be represented reliably.',code='numerical_range',status=422)
        half=noise+bound['value']
        data.update(total_half_width=frozen_array(half),lower=frozen_array(center-half),upper=frozen_array(center+half))
    return data,z


def joint_status(spectrum, cov, sigma, k):
    meta=spectrum.metadata();n=meta['shape'][1]
    if sigma==0: reason='zero_noise'
    elif k==0: reason='initialization'
    elif meta['numerical_rank']<n: reason='rank_deficient_or_unresolved'
    elif meta['eigenvalue_underflow_modes'] or np.min(cov['weights'])<=np.max(cov['weights'])*1e-12: reason='covariance_ill_conditioned'
    elif n>256: reason='joint_size_limit'
    else: reason='optional_joint_calculation_not_implemented'
    return dict(status='joint_region_unavailable',reason=reason)
