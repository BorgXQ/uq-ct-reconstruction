"""Truth-only evidence. Never recalibrates or modifies an inference result."""
import math
import numpy as np
from threadpoolctl import threadpool_limits
from ..ct.solver import frozen_array


def source_check(spectrum, truth, source):
    if source is None: return dict(status='not_supplied',reason='No source declaration to check.')
    meta=spectrum.metadata();V=spectrum.vectors;lam=spectrum.eigenvalues
    resolved=spectrum.singular_values>meta['rank_tolerance']
    norm=float(np.linalg.norm(truth));tolerance=1e-10*max(1,norm)
    with threadpool_limits(limits=1,user_api='blas'):
        coefficients=V.T@truth
        residual=truth-V[:,resolved]@coefficients[resolved]
        outside=truth-V@coefficients
    residual_norm=float(np.linalg.norm(residual))
    evidence=dict(numerical_residual=residual_norm,structural_residual=float(np.linalg.norm(outside)),
                  image_tolerance=tolerance,singular_tolerance=meta['rank_tolerance'],minimum_source_norm=None,
                  scope='This frozen image/operator and declared nu/M; float64 numerical evidence only.')
    if np.linalg.norm(outside)>tolerance:
        return dict(**evidence,status='violated',reason='Truth has a component outside the computed right subspace.')
    if residual_norm>tolerance:
        ambiguous=(spectrum.singular_values>0)&~resolved
        ambiguous_energy=float(np.linalg.norm(coefficients[ambiguous]))
        return dict(**evidence,status='unavailable' if ambiguous_energy>tolerance else 'violated',
                    reason='Significant truth component in numerical null/unresolved directions; source inversion is not reliable.')
    if norm==0: wnorm=0.0
    else:
        # Invert all representable positive modes; never silently discard tiny coefficients.
        positive=(lam>0)&(coefficients!=0)
        if np.any((spectrum.singular_values>0)&(lam==0)&(coefficients!=0)):
            return dict(**evidence,status='unavailable',reason='Positive eigenvalue underflow prevents source inversion.')
        logs=np.log(np.abs(coefficients[positive]))-source['nu']*np.log(lam[positive])
        if len(logs):
            largest=float(np.max(logs));lognorm=largest+.5*math.log(float(np.sum(np.exp(2*(logs-largest)))))
            if not math.isfinite(lognorm) or lognorm>math.log(np.finfo(float).max):
                return dict(**evidence,status='unavailable',reason='Source norm exceeds representable numerical range.')
            wnorm=math.exp(lognorm)
        else: wnorm=0.0
        if meta['ambiguous_modes'] and np.any(coefficients[(spectrum.singular_values>0)&~resolved]!=0):
            evidence['minimum_source_norm']=wnorm
            return dict(**evidence,status='unavailable',reason='Source inversion depends on unresolved positive modes; reported norm is only a numerical estimate.')
    evidence['minimum_source_norm']=wnorm
    ok=wnorm<=source['M']+1e-10*max(1,source['M'])
    return dict(**evidence,status='numerically_checked' if ok else 'violated',
                reason='Source representation and norm support the declaration within stated tolerances.' if ok else 'Minimum source norm exceeds the declared M.')


def diagnose(center, noiseless, truth, bound, lower=None, upper=None):
    bias=noiseless-truth;random=center-noiseless;error=center-truth
    norm=float(np.linalg.norm(error));truthnorm=float(np.linalg.norm(truth));bn=float(np.linalg.norm(bias))
    result=dict(error=frozen_array(error),absolute_error=frozen_array(np.abs(error)),actual_bias=frozen_array(bias),
                observed_random=frozen_array(random),truth=frozen_array(truth))
    meta=dict(actual_bias_norm=bn,error_norm=norm,rmse=norm/math.sqrt(len(truth)),
              relative_error=norm/truthnorm if truthnorm else None,relative_error_reason=None if truthnorm else 'zero_truth_norm',
              decomposition_residual=float(np.linalg.norm(error-bias-random)),
              bias_bound_supported=None if bound is None else bn<=bound+1e-10*max(1,bn,bound),
              inclusion_fraction=None,label='Validation only — single-run spatial inclusion, not repeated coverage')
    if lower is not None:
        inclusion=(lower<=truth)&(truth<=upper)
        result['inclusion']=frozen_array(inclusion.astype(float));meta['inclusion_fraction']=float(np.mean(inclusion))
    return result,meta
