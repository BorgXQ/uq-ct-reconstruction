"""Drawing-only source calibration; never uses measured data or noise."""
from collections import OrderedDict
import math
import numpy as np
from scipy.linalg import eigh
from ..ct.parallel import numerical_threads
from ..ct.geometry import fields, identifier, scalar


def source_norm(eigenvalues, coefficients, truth_norm, nu, eigen_tolerance, invisible_norm=0):
    tolerance=1e-10*max(1,truth_norm)
    result=dict(status='unresolved',M=None,nu=nu,eigenvalue_tolerance=eigen_tolerance,
                image_tolerance=tolerance,minimum_source_norm=None)
    if invisible_norm>tolerance:
        return dict(result,status='incompatible',reason='The drawing has nonzero density in pixels invisible to this acquisition; no finite M satisfies the source condition for positive ν.')
    if truth_norm==0:
        return dict(result,status='compatible',M=0.,minimum_source_norm=0.,reason='The zero drawing satisfies the source condition with M = 0.')
    unresolved=eigenvalues<=eigen_tolerance
    residual=float(np.linalg.norm(coefficients[unresolved]))
    result['unresolved_component_norm']=residual
    if np.any(unresolved):
        return dict(result,reason=f'Numerical null or weakly observed directions remain unresolved (drawing component norm {residual:.4g}). No M is supplied; a truncated spectrum would underestimate the source norm.')
    logs=np.log(np.abs(coefficients[coefficients!=0]))-nu*np.log(eigenvalues[coefficients!=0])
    top=float(np.max(logs));lognorm=top+.5*math.log(float(np.sum(np.exp(2*(logs-top)))))
    if not math.isfinite(lognorm) or lognorm>math.log(np.finfo(float).max)-1:
        return dict(result,reason='Source norm exceeds the representable numerical range.')
    minimum=math.exp(lognorm)
    return dict(result,status='compatible',minimum_source_norm=minimum,M=float(np.nextafter(minimum,np.inf)),
                reason='No numerical null-space directions detected. M calibrated from the drawing; float64 evidence, not a theoretical certificate.')


class SourceCalibration:
    def __init__(self,ct):
        self.ct=ct;self.cache=OrderedDict()

    def submit(self,data):
        fields(data,('request_id','observation_id','nu'),('request_id','observation_id','nu'))
        request_id=identifier(data['request_id'],'request_id');oid=identifier(data['observation_id'],'observation_id')
        nu=scalar(data['nu'],'nu')
        if nu<=0:
            from ..images import InputError
            raise InputError('Exponent ν must be positive.','nu')
        observation=self.ct._get('observations',oid).meta()
        acquisition=self.ct._get('acquisitions',observation['acquisition_id']).meta()
        spec=self.ct.specs[observation['operator_id']];sid=acquisition['snapshot_id']
        key=(spec.geometry_hash,acquisition['snapshot_content_hash'])
        with self.ct.lock: cached=self.cache.get(key)
        n=spec.shape[1]
        workspace=0 if cached is not None else 3*n*n*8+spec.estimate_bytes()+spec.shape[0]*min(32,n)*16
        def work(job):
            with self.ct.lock: entry=cached if cached is not None else self.cache.get(key)
            hit=entry is not None
            if entry is None:
                op,_=self.ct.operator(spec,job);truth=self.ct.snapshots.array(sid).ravel()
                invisible=float(np.linalg.norm(truth[np.asarray(op._matrix.sum(axis=0)).ravel()==0]))
                normal=np.empty((n,n),dtype=float,order='F')
                with numerical_threads():
                    for start in range(0,n,32):
                        job.check();size=min(32,n-start);basis=np.zeros((n,size));basis[start+np.arange(size),np.arange(size)]=1
                        normal[:,start:start+size]=op.adjoint_block(op.forward_block(basis))
                        job.progress('Building source-condition normal operator',start+size,n)
                    job.check();job.progress('Checking full source spectrum',0,1)
                    values,vectors=eigh(normal,overwrite_a=True,check_finite=False,driver='evr')
                    job.check();coefficients=vectors.T@truth
                tolerance=100*np.finfo(float).eps*n*max(float(values[-1]),1.)
                payload=(values,coefficients,float(np.linalg.norm(truth)),tolerance,invisible)
                size=values.nbytes+coefficients.nbytes
                with self.ct.lock:
                    while len(self.cache)>=16:
                        _,(_,lease)=self.cache.popitem(last=False);lease.close()
                    lease=self.ct.budget.reserve(size);self.cache[key]=(payload,lease)
                entry=(payload,lease)
            values,coefficients,norm,tolerance,invisible=entry[0]
            evidence=source_norm(values,coefficients,norm,nu,tolerance,invisible)
            evidence.update(observation_id=oid,operator_id=observation['operator_id'],snapshot_id=sid,
                snapshot_content_hash=acquisition['snapshot_content_hash'],cache_hit=hit,
                method='full_normal_eigh_source_v1',scope='Known drawing and selected acquisition only; other bias assumptions are not checked.')
            job.check();job.commit(lambda:evidence)
        return self.ct._submit('source_calibration',request_id,dict(observation_id=oid,nu=nu),[],workspace,work)
