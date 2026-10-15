"""Opt-in truth and exact-reference evidence, never an inference correction."""
import numpy as np
from ..ct.solver import frozen_array
from .numerics import NormalAction


def bias_diagnostics(op,factors,omega,k,truth,bound,eta,check=lambda:None):
    projected=factors.vectors@(factors.vectors.T@truth)
    components=np.column_stack((projected,truth-projected));normal=NormalAction(op,check)
    for _ in range(k):components-=omega*normal(components)
    a,c=components.T;an=float(np.linalg.norm(a));cn=float(np.linalg.norm(c));total=float(np.linalg.norm(a+c))
    tolerance=1e-10*max(1,float(np.linalg.norm(truth)))
    retained=None if bound is None else an<=bound+tolerance
    omitted=None if eta is None else cn<=eta*an+tolerance
    if eta is not None and an==0 and cn>0:omitted=False
    elif eta is not None and 0<an<=tolerance:omitted=None
    ratio=cn/an if an>tolerance else None
    ratio_status='finite' if ratio is not None else 'both_zero' if an==0 and cn==0 else 'zero_retained_nonzero_omitted' if an==0 else 'numerically_unresolved_near_zero'
    return dict(actual_bias=frozen_array(-a-c),retained_bias_vector=frozen_array(-a),omitted_bias_vector=frozen_array(-c)),dict(
        actual_retained_bias_norm=an,actual_omitted_bias_norm=cn,actual_bias_norm=total,
        retained_assumption_supported=retained,omitted_assumption_supported=omitted,
        omitted_retained_ratio=ratio,ratio_status=ratio_status,tolerance=tolerance,
        propagated_inner_product=float(a@c),scope='This frozen image, factors, full operator and inspected k only.')


def residual_evidence(full,retained,estimate,corrected,epsilon):
    exact=full-retained;error=estimate-exact;difference=corrected-full
    # Relative event at zero has no positive denominator floor. Tolerance is reported separately.
    event=np.abs(error)<=epsilon*np.abs(exact)
    tol=1e-10*max(float(np.max(full)),np.finfo(float).tiny)
    return dict(exact_residual=frozen_array(exact),residual_estimation_error=frozen_array(error),
        correction_minus_full=frozen_array(difference),relative_event_observed=frozen_array(event.astype(float))),dict(
        label='Numerically checked for this realization; not a probability certificate',
        relative_event_failures=int(np.count_nonzero(~event)),zero_residual_pixels=int(np.count_nonzero(exact==0)),
        correction_underestimated_pixels=int(np.count_nonzero(difference < -tol)),domination_tolerance=tol,
        maximum_absolute_estimation_error=float(np.max(np.abs(error))),control_established=False)
