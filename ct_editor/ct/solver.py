"""Pure full-operator Landweber. No truth, simulator or RNG dependency."""
import math
from time import perf_counter
import numpy as np
from ..images import InputError
from .geometry import fields, scalar, integer


def frozen_array(x):
    x=np.asarray(x,dtype='<f8')
    if not np.isfinite(x).all(): raise InputError('Nonfinite numerical output.',code='nonfinite_calculation',status=422)
    return np.frombuffer(x.tobytes(),dtype='<f8').reshape(x.shape)


def resolve_step(operator, settings):
    fields(settings,('mode','value'),('mode',))
    L=operator.metadata()['L_safe']
    if not np.isfinite(L) or L<=0: raise InputError('All selected rays miss or the operator has an invalid norm.',code='degenerate_operator',status=422)
    if settings['mode']=='automatic_safe':
        if 'value' in settings: raise InputError('Automatic step cannot include a manual value.','value')
        omega=1/(2*L);policy='safe_induced_norm_v1'
    elif settings['mode']=='manual':
        omega=scalar(settings.get('value'),'value');policy='manual_safe_bound_v1'
        if not 0<omega<2/L: raise InputError(f'Manual step must satisfy 0 < omega < {2/L:.17g}.','value','invalid_step',422)
    else: raise InputError('Only automatic_safe and manual step modes are supported.','mode')
    return dict(omega=omega,step_policy=policy,L_safe=L,stable_certified=True,nonoscillatory_certified=omega<=1/L)


def landweber(operator, y, omega, iterations, cancel=lambda:False, emit=lambda *args:None):
    integer(iterations,'iterations',1,5000)
    y=frozen_array(y)
    if y.shape!=(operator.shape[0],): raise InputError('Observation shape mismatch.',code='incompatible_dimensions')
    resolve_step(operator,{'mode':'manual','value':omega})
    x=np.zeros(operator.shape[1],np.float64);residual=y.copy();ynorm=float(np.linalg.norm(y));start=perf_counter()
    stride=max(1,math.ceil(iterations/100));checkpoints={};history=[];k=0
    def record():
        norm=float(np.linalg.norm(residual));objective=.5*norm*norm
        if not np.isfinite(norm) or not np.isfinite(objective): raise InputError('Nonfinite reconstruction metric.',code='nonfinite_calculation',status=422)
        history.append(dict(iteration=k,residual_norm=norm,objective=objective,relative_residual=norm/ynorm if ynorm else None,
            relative_residual_reason=None if ynorm else 'zero_observation_norm',elapsed_seconds=perf_counter()-start))
        if k%stride==0 or k==iterations: checkpoints[k]=frozen_array(x)
        emit(k,frozen_array(x))  # Non-controlling, immutable diagnostic copy.
    record()
    while k<iterations and not cancel():
        x=x+omega*operator.adjoint(residual)
        if not np.isfinite(x).all(): raise InputError('Nonfinite reconstruction.',code='nonfinite_calculation',status=422)
        residual=y-operator.forward(x);k+=1;record()
    checkpoints[k]=frozen_array(x)
    return dict(final=checkpoints[k],history=history,checkpoints=checkpoints,completed_iterations=k,
        requested_iterations=iterations,status='completed' if k==iterations else 'canceled',
        stop_reason='requested_iterations' if k==iterations else 'user_canceled',checkpoint_stride=stride,
        elapsed_seconds=perf_counter()-start,forward_applications=k,adjoint_applications=k)


def replay(operator,y,omega,initial,start,target,check=lambda:None,progress=lambda *args:None):
    x=np.array(initial,dtype=np.float64,copy=True)
    for k in range(start,target):
        check();x+=omega*operator.adjoint(y-operator.forward(x));progress('Replaying checkpoint',k+1-start,target-start)
    return frozen_array(x)
