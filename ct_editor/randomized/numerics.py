"""Noise-value/truth-free M4 kernels. All random samples have indexed role streams."""
import math
from dataclasses import dataclass
from time import perf_counter
import numpy as np
from scipy.linalg import svd, eigh
from threadpoolctl import threadpool_limits
from ..images import InputError
from ..ct.geometry import integer, scalar
from ..ct.service import Record, digest
from ..ct.solver import frozen_array
from ..ct.jobs import Canceled
from ..exact.numerics import filters, covariance, bias_bound, intervals

VERSION = 'gaussian_normal_projected_v1'
ACTION = 'matrix_free_incremental_covariance_v1'


def stream(seed, role, index):
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(role,index))))


def samples(n, count, seed, role, start=0):
    columns=[]
    for j in range(start,start+count):
        rng=stream(seed,role,j)
        columns.append(rng.standard_normal(n) if role==1 else 2*rng.integers(0,2,n)-1)
    return np.asarray(columns,dtype=float).T


def probe_policy(n,count,seed,block):
    policy=dict(n=n,count=count,root_seed=seed,role='rademacher',stream_role=2,
                generator='PCG64',derivation='SeedSequence(root, spawn_key=(role,index))',version=1)
    return dict(**policy,probe_id=digest(policy),block_size=block)


@dataclass(frozen=True)
class Factors:
    """Same read-only filter interface as FullSpectrum, distinct record/identity."""
    record: Record
    @property
    def eigenvalues(self): return self.record.array('eigenvalues')
    @property
    def vectors(self):
        m=self.metadata();return self.record.array('vectors').reshape(m['n'],m['effective_rank'])
    @property
    def squared_vectors(self): return self.vectors**2
    def metadata(self): return self.record.meta()


def factor_record(values,vectors,metadata=None):
    meta=dict(n=vectors.shape[0],effective_rank=len(values),eigenvalue_underflow_modes=0,**(metadata or {}))
    meta['factor_id']=digest({key:value for key,value in meta.items() if key!='elapsed_seconds'},values,vectors)
    return Factors(Record.make(meta,eigenvalues=values,vectors=vectors))


class NormalAction:
    def __init__(self,op,check=lambda:None): self.op=op;self.check=check;self.columns=0
    def __call__(self,x):
        self.check();self.columns+=1 if x.ndim==1 else x.shape[1]
        if x.ndim==2 and hasattr(self.op,'forward_block'):
            out=self.op.adjoint_block(self.op.forward_block(x))
        else: out=self.op.adjoint(self.op.forward(x))
        if not np.isfinite(out).all(): raise InputError('Nonfinite full normal action.',code='numerical_range',status=422)
        return out


def gaussian_factors(op,r,p,seed,block=8,check=lambda:None,progress=lambda *a:None, *, seed_entropy=None):
    n=op.shape[1];integer(r,'rank',1,min(n,512));integer(p,'oversampling',4,64)
    if r+p>n: raise InputError('Require rank + oversampling ≤ image dimension.','rank')
    integer(seed,'sketch_seed',0,2**32-1);integer(block,'block_size',1,64)
    if seed_entropy is not None:
        if not isinstance(seed_entropy,list) or len(seed_entropy)!=4:
            raise InputError('Expected four validation seed words.')
        for word in seed_entropy: integer(word,'seed word',0,2**32-1)
        seed=seed_entropy
    started=perf_counter();normal=NormalAction(op,check);ell=r+p
    Y=np.empty((n,ell))
    for start in range(0,ell,block):
        end=min(start+block,ell);Y[:,start:end]=normal(samples(n,end-start,seed,1,start))
        progress('Sampling Gaussian subspace',end,ell)
    with threadpool_limits(limits=1,user_api='blas'):
        check();progress('Orthonormalizing sketch',0,None)
        # Thin SVD of n×ell sampled range only; never SVD of A or dense B.
        q,s,_=svd(Y,full_matrices=False,check_finite=False)
        tolerance=max(Y.shape)*np.finfo(float).eps*(s[0] if len(s) else 0)
        effective=int(np.count_nonzero(s>tolerance));q=q[:,:effective]
        if effective<r: raise InputError(f'Sampled basis supports {effective} modes, requested {r}. Explicitly choose a lower rank.',code='sample_rank_deficient',status=422)
        H=np.zeros((effective,effective))
        for start in range(0,effective,block):
            end=min(start+block,effective);H[:,start:end]=q.T@normal(q[:,start:end])
        asym=float(np.linalg.norm(H-H.T)/max(np.linalg.norm(H),np.finfo(float).tiny))
        if asym>1e-10: raise InputError('Projected operator is not numerically symmetric.',code='numerical_range',status=422)
        check();progress('Decomposing projected operator',0,None)
        values,u=eigh((H+H.T)/2,check_finite=False);order=np.argsort(values)[::-1];values=values[order];u=u[:,order]
        psdtol=max(1,effective)*np.finfo(float).eps*max(abs(values[0]),np.finfo(float).tiny)
        if np.min(values)<-10*psdtol: raise InputError('Projected operator is not positive semidefinite.',code='numerical_range',status=422)
        supported=int(np.count_nonzero(values>psdtol))
        if supported<r: raise InputError(f'Only {supported} projected positive modes resolved; requested {r}.',code='sample_rank_deficient',status=422)
        vectors=q@u[:,:r];values=values[:r]
        orth=float(np.linalg.norm(vectors.T@vectors-np.eye(r)))
        if orth>1e-10 or values[0]>op.metadata()['L_safe']*(1+1e-10):
            raise InputError('Randomized factor quality check failed.',code='numerical_range',status=422)
        check()
        return factor_record(values,vectors,dict(operator_id=op.metadata()['operator_id'],csr_checksum=op.metadata()['csr_checksum'],
            algorithm=VERSION,requested_rank=r,oversampling=p,sample_width=ell,basis_dimension=effective,
            sample_rank_loss=effective<ell,rank_tolerance=float(tolerance),psd_tolerance=float(psdtol),
            orthogonality_residual=orth,symmetry_residual=asym,projected_positive_rank=supported,
            sketch_seed=seed,stream_role=1,generator='PCG64',stream_policy='SeedSequence(root, spawn_key=(1,column))',
            block_size=block,normal_column_actions=normal.columns,elapsed_seconds=perf_counter()-started,
            warnings=['Oversampled range lost dimensions; event applicability unresolved.'] if effective<ell else []))


def retained_action(factors,omega,k,sigma,x):
    _,a=filters(factors.eigenvalues,omega,k);weights=sigma**2*a;v=factors.vectors
    coords=v.T@x
    return v@(weights*coords if x.ndim==1 else weights[:,None]*coords)


def direct_covariance(op,omega,k,sigma,x,check=lambda:None):
    """S_k B S_k reference, supports a vector or bounded block."""
    integer(k,'iteration',0,5000);scalar(sigma,'sigma')
    x=np.asarray(x,dtype=float)
    if x.ndim not in (1,2) or x.shape[0]!=op.shape[1] or not np.isfinite(x).all(): raise InputError('Invalid covariance action vector/block.')
    if not math.isfinite(omega) or omega<=0: raise InputError('Invalid covariance step.')
    if sigma==0 or k==0: return np.zeros_like(x)
    normal=NormalAction(op,check)
    def total(v):
        u=np.zeros_like(v)
        for _ in range(k): u=u-omega*normal(u)+v
        return u
    return frozen_array(sigma**2*omega**2*total(normal(total(x))))


class StreamingCovariance:
    """Exact polynomial recurrence, avoids subtracting 2 S_k - S_2k.

    h_k=G^k S_k B Z; t_k=G^(2k) B Z.
    C_(k+1)=C_k+σ²ω²(2h_k+t_k), h_(k+1)=G h_k+G t_k,
    t_(k+1)=G²t_k. Uses three normal block actions per increment.
    """
    def __init__(self,op,omega,sigma,z,check=lambda:None):
        self.normal=NormalAction(op,check);self.omega=omega;self.scale=sigma**2*omega**2
        self.t=self.normal(z) if sigma else np.zeros_like(z)
        self.h=np.zeros_like(z);self.value=np.zeros_like(z);self.k=0
    def advance(self):
        self.normal.check();self.k+=1
        if self.scale==0:return self.value
        self.value+=self.scale*(2*self.h+self.t)
        gt=self.t-self.omega*self.normal(self.t)
        self.h=self.h-self.omega*self.normal(self.h)+gt
        self.t=gt-self.omega*self.normal(gt)
        if not np.isfinite(self.value).all():raise InputError('Covariance recurrence overflow.',code='numerical_range',status=422)
        return self.value


def tsvd_split(spectrum,omega,k,sigma,source,r):
    q=int(np.count_nonzero(spectrum.eigenvalues>0))
    if not 1<=r<q: raise InputError('Exact TSVD requires rank smaller than computed positive rank.',code='tsvd_rank_unavailable',status=422)
    retained=factor_record(spectrum.eigenvalues[:r],spectrum.vectors[:,:r])
    tail=factor_record(spectrum.eigenvalues[r:],spectrum.vectors[:,r:])
    cr=covariance(retained,omega,k,sigma);ct=covariance(tail,omega,k,sigma)
    step=dict(omega=omega,step_policy='spectral');br=bias_bound(retained,step,k,source,'spectral');bt=bias_bound(tail,step,k,source,'spectral')
    combined=None
    if br is not None and bt is not None:
        if br['numerical_status'] not in ('finite','exact_zero') or bt['numerical_status'] not in ('finite','exact_zero'):
            raise InputError('TSVD bias allowance outside numerical range.',code='numerical_range',status=422)
        combined=math.hypot(br['value'],bt['value'])
    return dict(tsvd_retained_variance=cr['variance'],tsvd_omitted_variance=ct['variance']),dict(
        retained_bias=br,tail_bias=bt,combined_bias=combined,tail_policy='direct_spectral_max',
        covariance_addition_error=float(np.max(np.abs(cr['variance']+ct['variance']-covariance(spectrum,omega,k,sigma)['variance']))))


def corrected_quantities(factors,omega,k,sigma,source,estimate,epsilon):
    retained=covariance(factors,omega,k,sigma);allowance=np.abs(estimate)/(1-epsilon)
    variance=retained['variance']+allowance
    if not np.isfinite(variance).all(): raise InputError('Corrected variance overflow.',code='numerical_range',status=422)
    bound=bias_bound(factors,dict(omega=omega,step_policy='spectral'),k,source,'spectral')
    return dict(retained_variance=retained['variance'],retained_sd=retained['pixel_sd'],
                signed_residual=frozen_array(estimate),residual_allowance=frozen_array(allowance),
                variance=frozen_array(variance),pixel_sd=frozen_array(np.sqrt(variance))),bound


def evaluate(op,factors,omega,sigma,k,policy,epsilon,source,check=lambda:None,progress=lambda *a:None,spectrum=None):
    """Fixed-k signed block average. No partial estimate is returned as complete."""
    n=op.shape[1];count=policy['count'];acc=np.zeros(n);completed=0
    started=perf_counter();full_seconds=0.;retained_seconds=0.
    for start in range(0,count,policy['block_size']):
        check();size=min(policy['block_size'],count-start)
        z=samples(n,size,policy['root_seed'],2,start)
        begin=perf_counter()
        full=retained_action(spectrum,omega,k,sigma,z) if spectrum else direct_covariance(op,omega,k,sigma,z,check)
        full_seconds+=perf_counter()-begin;begin=perf_counter()
        acc+=np.sum(z*(full-retained_action(factors,omega,k,sigma,z)),axis=1);completed+=size
        retained_seconds+=perf_counter()-begin
        progress('Probing covariance residual',completed,count)
    arrays,bound=corrected_quantities(factors,omega,k,sigma,source,acc/count,epsilon)
    return arrays,bound,dict(completed_probes=completed,elapsed_seconds=perf_counter()-started,
        backend='full_spectrum_reference' if spectrum else 'matrix_free_direct_v1',
        full_action_seconds=full_seconds,retained_and_accumulation_seconds=retained_seconds,
        normal_column_actions=0 if spectrum or sigma==0 or k==0 else (2*k+1)*count)


def randomized_crossing(op,factors,omega,sigma,source,policy,epsilon,budget,check=lambda:None,progress=lambda *a:None,spectrum=None):
    if source is None:raise InputError('Randomized stopping requires a source declaration.',code='source_missing',status=422)
    history=[];n=op.shape[1];blocks=[];last=None
    try:
        for start in range(0,policy['count'],policy['block_size']):
            check();size=min(policy['block_size'],policy['count']-start);z=samples(n,size,policy['root_seed'],2,start)
            blocks.append((z,None if spectrum else StreamingCovariance(op,omega,sigma,z,check)))
        for k in range(1,budget+1):
            acc=np.zeros(n);completed=0
            for z,rec in blocks:
                check();full=retained_action(spectrum,omega,k,sigma,z) if spectrum else rec.advance()
                acc+=np.sum(z*(full-retained_action(factors,omega,k,sigma,z)),axis=1);completed+=z.shape[1]
                progress('Probing candidate covariance',completed,policy['count'])
            arrays,b=corrected_quantities(factors,omega,k,sigma,source,acc/policy['count'],epsilon)
            v=float(np.max(arrays['pixel_sd']))
            crossed=b['exact_zero'] or (v>0 and (b['value']<=v if b['numerical_status']=='finite' else b['log_value']<=math.log(v)))
            history.append(dict(iteration=k,bias_bound=b['value'],log_bias=b['log_value'],numerical_status=b['numerical_status'],
                threshold=v,max_pixel_sd=v,crossed=crossed,requested_probes=policy['count'],completed_probes=completed,probe_id=policy['probe_id'],
                backend='full_spectrum_reference' if spectrum else ACTION,control_mode='unverified_relative_target',
                iteration_scope='selected_k_same_probes'))
            progress('Searching randomized crossing',k,budget);last=(arrays,b)
            if crossed:
                return dict(status='criterion_met',selected_k=k,history=history,last_evaluated_k=k),last
    except Canceled:
        return dict(status='canceled',selected_k=None,history=history,last_evaluated_k=len(history)),None
    return dict(status='criterion_not_met',selected_k=None,history=history,last_evaluated_k=budget),None


def assemble(center,arrays,bound,eta,alpha):
    total=None
    if bound is not None and eta is not None:
        value=None if bound['value'] is None else (1+eta['value'])*bound['value']
        total={**bound,'value':value,'method':'randomized_retained_plus_omitted',
               'log_value':None if bound['exact_zero'] else bound['log_value']+math.log1p(eta['value'])}
        if value is not None and not math.isfinite(value):raise InputError('Inflated bias overflow.',code='numerical_range',status=422)
    out,z=intervals(center,arrays,total,alpha)
    return {**arrays,**out},dict(retained_bias_allowance=bound,total_bias_allowance=total,gaussian_quantile=z,
        intervals_available=total is not None,intervals_unavailable_reason=None if total else 'initialization_or_missing_source_or_eta')


def probability(config,factors,k):
    p=config['oversampling'];alpha=config['alpha'];delta=config['delta']
    factors_probability=[1-alpha,1-3*math.exp(-p),1-delta]
    scope='selected_k_same_probes' if config['selection']['mode']=='automatic' else 'fixed_k'
    return dict(nominal_gaussian_level=1-alpha,reference_randomized_coverage_expression=math.prod(factors_probability),
        reference_coverage_factors=factors_probability,coverage_expression_applicable=False,empirical_coverage=None,
        empirical_coverage_reason='not_run_in_M4',source_condition_status='assumed_unverified' if config['source'] else 'not_supplied',
        retained_bias_assumption_status='assumed_unverified' if config['source'] else 'not_supplied',
        omitted_bias_assumption_status='assumed_unverified' if config['eta'] else 'not_supplied',
        sketch_event_status='spectral_threshold_unavailable',selection_status=config['selection']['provenance'],
        settings_origin=config['settings_origin'],numerical_status='checked',
        coverage_interpretation='Exploratory corrected formula — probability conditions not established',
        unmet_conditions=['Source condition and both Assumption 3.9 inequalities require justification.',
            'Quantitative beta calculation and hypothesis checks are not implemented.',
            'No validated conditional relative-error probe-count procedure is supplied.',
            'Same-probe stopping requires selected-iteration control; fixed-k control is insufficient.' if scope.startswith('selected') else 'Observed probe accuracy is not a concentration certificate.',
            'Settings independence from measurement noise is declared or unknown, not proven.'],
        probe_control=dict(control_mode='unverified_relative_target',epsilon_target=config['epsilon'],delta_target=delta,
            probe_count_requested=config['probe_count'],probe_count_completed=config['probe_count'] if k is not None else 0,
            conditioning='fixed sketch, operator, source and settings',iteration_scope=scope,pixel_scope='each fixed pixel; not simultaneous',
            provider=None,control_established=False,reason='Applicable conditional concentration procedure unavailable.'),
        appendix=dict(status='spectral_threshold_unavailable',beta=None,rho=None,
            rho_status='full_lower_spectrum_unavailable',approximate_complement_norm=1,
            reason='Quantitative β certification is not implemented. Stable full steps alone do not establish ρ < 1.'))
