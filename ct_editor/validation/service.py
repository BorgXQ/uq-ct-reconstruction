"""Capture plans and execute within the established single numerical worker."""
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4
from time import perf_counter
import json
import numpy as np
from ..images import InputError
from ..ct.geometry import Geometry,Operator,fields,integer,scalar,identifier
from ..ct.solver import resolve_step,validate_step
from ..ct.service import digest
from ..ct.jobs import Canceled,now
from ..exact.numerics import source_config,quantile,compatible_bound
from ..randomized.numerics import factor_record
from .engine import experiment,METHODS,SEED_VERSION


def plain(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,dict):return {str(k):plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [plain(v) for v in value]
    return value


def size(value):
    if isinstance(value,np.ndarray):return value.nbytes
    if isinstance(value,dict):return sum(len(str(k))+size(v) for k,v in value.items())
    if isinstance(value,(tuple,list)):return sum(size(v) for v in value)
    return len(str(value))+16


def choices(value,allowed,name):
    if value not in allowed:raise InputError(f'{name} must be one of {", ".join(allowed)}.',name)
    return value


def validate_plan(p,c,n):
    choices(p['protocol'],('fixed_algorithm','all_random_sources'),'protocol')
    choices(p['comparison'],('common_k','own_stopping'),'comparison')
    choices(p['purpose'],('predeclared_validation','exploratory_study'),'purpose')
    choices(p['scope'],('intervals','moments'),'scope')
    choices(p['diagnostics'],('none','representative','all'),'diagnostics')
    integer(p['trials'],'trials',1,10000);integer(p['root_seed'],'root_seed',0,2**32-1)
    integer(p['configuration_index'],'configuration_index',0,15)
    integer(p['iterations'],'iterations',1,None);integer(p['budget'],'budget',1,None)
    if not 0<scalar(p['c0'],'c0'):raise InputError('c0 must be positive.')
    quantile(p['mc_alpha']);quantile(c['alpha']);scalar(c['sigma'],'sigma')
    if not isinstance(p['methods'],list) or not p['methods'] or len(p['methods'])>4 or len(set(p['methods']))!=len(p['methods']):raise InputError('Choose distinct supported methods.')
    for m in p['methods']:choices(m,METHODS,'method')
    for key,limit,maximum in [('pixels',16,n-1),('representatives',8,p['trials']-1),('diagnostic_iterations',32,None)]:
        values=p[key]
        if not isinstance(values,list) or len(values)>limit or len(set(values))!=len(values):raise InputError(f'Invalid {key}.')
        for value in values:integer(value,key,1 if key=='diagnostic_iterations' else 0,maximum)
    c['source']=source_config(c['source']);compatible_bound(c['bound_method'],dict(step_policy=c['step_policy']))
    choices(c['backend'],('matrix_free','full_spectrum_reference'),'backend')
    if p['comparison']=='own_stopping' and c['source'] is None:raise InputError('Own stopping requires declared source parameters.')
    if p['scope']=='intervals' and (c['source'] is None or ('corrected' in p['methods'] and c.get('eta') is None)):
        raise InputError('Coverage requires source and corrected-method eta declarations. Choose moments only or complete the template.')
    if c.get('eta') is not None:
        fields(c['eta'],('value','provenance'),('value','provenance'));scalar(c['eta']['value'],'eta');identifier(c['eta']['provenance'],'eta provenance')
    for key,lo,hi in [('rank',1,min(n,512)),('oversampling',4,64),('probe_count',1,1024),('block_size',1,64),('sketch_seed',0,2**32-1),('probe_seed',0,2**32-1)]:integer(c[key],key,lo,hi)
    if c['rank']+c['oversampling']>n:raise InputError('Rank + oversampling must fit the numerical image.')
    for key in ('epsilon','delta'):
        if not 0<scalar(c[key],key)<1:raise InputError(f'{key} must be between zero and one.')
    if type(c['reference']) is not bool:raise InputError('Reference must be Boolean.')


class ValidationService:
    def __init__(self,randomized,config):
        self.randomized=randomized;self.exact=randomized.exact;self.ct=randomized.ct;self.lock=RLock()
        self.records={};self.pending={};self.grids={};self.replays={};self.leases=[]
        self.limit=config.get('VALIDATION_LIMIT',24)
        self.max_trials=config.get('VALIDATION_MAX_TOTAL_TRIALS',10000)
        self.directory=Path(config.get('VALIDATION_ARTIFACT_DIR','.artifacts')).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.disk_limit=config.get('VALIDATION_DISK_BYTES',512*1024**2)

    def capture(self,data):
        allowed=('request_id','template_id','template_kind','protocol','comparison','purpose','scope','trials','root_seed','methods','iterations','budget','c0','mc_alpha','pixels','representatives','diagnostics','diagnostic_iterations','reference')
        fields(data,allowed,('request_id','template_id'))
        identifier(data['request_id'],'request_id');kind=choices(data.get('template_kind','randomized'),('randomized','exact'),'template_kind')
        record=self.ct._get('randomized_analyses' if kind=='randomized' else 'exact_analyses',identifier(data['template_id'],'template_id')).meta()
        if record['status']!='completed':raise InputError('Choose a completed analysis template.')
        c=deepcopy(record['configuration']);o=self.ct._get('observations',c['observation_id']).meta()
        acquisition=self.ct._get('acquisitions',o['acquisition_id']).meta();spec=self.ct.specs[o['operator_id']]
        defaults=dict(rank=2,oversampling=4,probe_count=32,block_size=8,sketch_seed=0,probe_seed=0,epsilon=.2,delta=.01,eta=None,backend='matrix_free',reference=False)
        for key,value in defaults.items():c.setdefault(key,value)
        if 'reference' in data:c['reference']=data['reference']
        p=dict(protocol='fixed_algorithm',comparison='common_k',purpose='exploratory_study',scope='intervals',trials=100,root_seed=2026,
            methods=['corrected'] if kind=='randomized' else ['exact'],iterations=record['iteration'],budget=1000,c0=1.,mc_alpha=.05,
            pixels=[0],representatives=[0],diagnostics='representative',diagnostic_iterations=[],configuration_index=0)
        p.update({k:deepcopy(data[k]) for k in p if k in data})
        p.update(template_id=data['template_id'],template_kind=kind,snapshot_id=acquisition['snapshot_id'],snapshot_hash=acquisition['snapshot_content_hash'],
            geometry=spec.metadata(),operator=acquisition['operator'],seed_version=SEED_VERSION,coupling='Shared observation within configuration; independent configuration streams',
            selection_provenance=c['selection']['provenance'],failure_policy='strict_selection_no_fallback',created_at=now(),schema='validation_plan_v1')
        validate_plan(p,c,spec.shape[1]);truth=self.ct.snapshots.array(acquisition['snapshot_id']).ravel().copy()
        step=deepcopy(record['step']);p['step_policy']=step['step_policy']
        return p,c,spec,truth,step

    def estimate(self,p,c,spec):
        n=spec.shape[1];m=spec.shape[0];K=p['budget'] if p['comparison']=='own_stopping' else p['iterations']
        random=bool(set(p['methods'])&{'corrected','retained'});repetitions=p['trials'] if p['protocol']=='all_random_sources' else 1
        cfg={**c,'selection':{'mode':'automatic','budget':K} if p['comparison']=='own_stopping' else {'mode':'fixed','iterations':K}}
        workspace=spec.estimate_bytes();work=2*m*(2*spec.resolution+1)*K*p['trials']*(2 if p['comparison']=='own_stopping' else 1)
        if random:
            rw,detail=self.randomized.preflight(spec,{**cfg,'reference':False,'backend':'matrix_free'})
            workspace=max(workspace,rw);work+=detail['estimated_scalar_work']*repetitions
        exact_reason=None;reference_workspace=0
        if c['reference'] or set(p['methods'])&{'exact','tsvd'} or c['backend']=='full_spectrum_reference' or c['step_policy']=='exact_spectral_half':
            try:reference_workspace=self.exact.preflight(spec,K)
            except InputError as exc:
                exact_reason=str(exc)
                if c['backend']=='full_spectrum_reference' or c['step_policy']=='exact_spectral_half':raise
            workspace=max(workspace,reference_workspace)
            if reference_workspace:work+=m*n*min(m,n)*2
        diag_count=p['trials'] if p['diagnostics']=='all' else len(p['representatives']) if p['diagnostics']=='representative' else 0
        work+=m*(2*spec.resolution+1)*(K*(3*diag_count+len(p['methods']))+sum(p['diagnostic_iterations']))*2
        retained=n*len(p['methods'])*200+p['trials']*len(p['methods'])*(8000+len(p['pixels'])*700)+len(p['representatives'])*(m*16+n*96*len(p['methods']))
        workspace+=retained*2+4*1024**2
        if p['trials']>self.max_trials:raise InputError(f'Trial count exceeds limit {self.max_trials:,}.',code='resource_limit',status=507)
        if workspace>self.ct.budget.limit:raise InputError('Study exceeds shared memory limit.',code='resource_limit',status=507)
        return dict(estimated_scalar_work=work,workspace_bytes=workspace,retained_estimate_bytes=retained,reference_reason=exact_reason,
            note='Conservative work estimate, not a predicted runtime; shared center work is reused.')

    def preflight(self,data):
        p,c,spec,_,step=self.capture(data);estimate=self.estimate(p,c,spec)
        return plain(dict(plan=p,configuration=c,step=step,estimate=estimate))

    def publish(self,sid,result):
        lease=self.ct.budget.reserve(size(result))
        with self.lock:self.leases.append(lease);self.records[sid]=result
        return dict(study_id=sid)

    def run(self,sid,p,c,spec,truth,step,job,operator=None,factors=None,stored_observations=None,indices=None):
        t=perf_counter();op,hit=(operator,False) if operator is not None else self.ct.operator(spec,job)
        sp=None;reason=None;ref_hit=False
        if c['reference'] or set(p['methods'])&{'exact','tsvd'} or c['backend']=='full_spectrum_reference' or c['step_policy']=='exact_spectral_half':
            try:
                self.exact.preflight(spec,p['budget'] if p['comparison']=='own_stopping' else p['iterations']);sp,ref_hit=self.exact.spectrum(op,job)
            except InputError as exc:
                if c['backend']=='full_spectrum_reference' or c['step_policy']=='exact_spectral_half':raise
                reason=str(exc)
        validate_step(op,step['omega'],sp.certificate() if sp else None)
        p=deepcopy(p);p['reference_reason']=reason
        result=experiment(op,sp,truth,p,c,step,job,factors,stored_observations,indices)
        a=op.sparse_copy();result.update(study_id=sid,created_at=now(),imported=False,reference_reason=reason,
            inputs=dict(truth=truth.copy(),clean=op.forward(truth),csr_data=a.data.copy(),csr_indices=a.indices.copy(),csr_indptr=a.indptr.copy(),csr_shape=list(a.shape)),
            provenance=dict(operator=op.metadata(),geometry=spec.metadata(),snapshot_hash=p['snapshot_hash'],seed_version=SEED_VERSION))
        result['timing'].update(operator_reference_seconds=perf_counter()-t-result['timing']['total_seconds'],operator_cache_hit=hit,spectrum_cache_hit=ref_hit)
        return result

    def submit(self,data):
        p,c,spec,truth,step=self.capture(deepcopy(data));estimate=self.estimate(p,c,spec);sid=uuid4().hex
        with self.lock:
            if len(self.records)+len(self.pending)>=self.limit:raise InputError('Validation study capacity reached.',code='storage_limit',status=507)
            self.pending[sid]=dict(study_id=sid,status='queued',plan=p,estimate=estimate)
        try:lease=self.ct.budget.reserve(estimate['workspace_bytes'])
        except Exception:
            with self.lock:self.pending.pop(sid,None)
            raise
        def cleanup():
            lease.close()
            with self.lock:self.pending.pop(sid,None)
        def work(job):
            result=self.run(sid,p,c,spec,truth,step,job)
            job.commit(lambda:self.publish(sid,result),status='canceled' if result['status']=='partial_canceled' else 'completed')
        try:
            job=self.ct.runner.submit('validation',data['request_id'],dict(study_id=sid,plan=p,estimate=estimate),work,cleanup)
            with self.lock:self.pending[sid]['job_id']=job['job_id']
            return {**job,'study_id':sid}
        except Exception:cleanup();raise

    def get(self,sid):
        with self.lock:
            if sid not in self.records:raise InputError('Study not retained in this process. Restore an exported bundle.',code='not_found',status=404)
            return self.records[sid]

    def response(self,sid,arrays=False):
        r=self.get(sid)
        keys=('study_id','status','created_at','plan','configuration','step','completed_trials','summaries','conditioning','interpretation','timing','source_check','retention','reference_reason','imported','bias_curve','provenance')
        out={k:r.get(k) for k in keys}
        out['representatives']=[{k:v for k,v in a.items() if not isinstance(v,np.ndarray)} for a in r['representatives'].values()]
        out['paired_comparisons']={k:dict(count=len(v),mean_width_difference=float(np.mean([x['width_difference'] for x in v])),mean_spatial_inclusion_difference=float(np.mean([x['spatial_inclusion_difference'] for x in v]))) for k,v in r['pairs'].items()}
        if arrays:out.update(maps=r['maps'],truth=r['inputs']['truth'])
        return plain(out)

    def pixel(self,sid,index):
        r=self.get(sid);n=len(r['inputs']['truth']);integer(index,'pixel',0,n-1)
        return plain(dict(study_id=sid,pixel=index,truth=r['inputs']['truth'][index],protocol=r['plan']['protocol'],conditioning=r['conditioning'],
            methods={m:dict(summary=r['summaries'][m],values={k:None if v is None else v[index] for k,v in r['maps'][m].items()},
            series=r['series'][m].get(str(index)),series_reason=None if str(index) in r['series'][m] else 'Not retained; explicitly replay this pixel from the original schedule') for m in r['plan']['methods']}))

    def grid(self,data,preview=False):
        fields(data,('request_id','base','axis','values','accepted_hash'),('request_id','base','axis','values'))
        axis=choices(data['axis'],('sigma','n_angles','rank','probe_count'),'axis');values=data['values']
        if not isinstance(values,list) or not 1<=len(values)<=16:raise InputError('Supply 1–16 explicit configuration values.')
        base,c0,spec0,truth,step0=self.capture(data['base']);rows=[];total=0;peak=0
        for i,value in enumerate(values):
            p=deepcopy(base);c=deepcopy(c0);spec=spec0;step=deepcopy(step0);p['configuration_index']=i
            if axis=='n_angles':
                integer(value,'n_angles',1,360);rg=spec0.metadata()['range_degrees']
                spec=Geometry.request(spec0.resolution,dict(n_angles=value,n_detectors=len(spec0.offsets),start_degrees=rg[0],end_degrees=rg[1]))
                # A changed operator requires a newly resolved safe step, explicitly recorded.
                if c['step_policy']!='safe_induced_norm_v1':raise InputError('Angle studies currently require the declared safe step policy.')
                p['geometry']=spec.metadata()
            else:c[axis]=value
            validate_plan(p,c,spec.shape[1]);e=self.estimate(p,c,spec);total+=e['estimated_scalar_work'];peak=max(peak,e['workspace_bytes'])
            rows.append((p,c,spec,step,e))
        if len(rows)*base['trials']>self.max_trials:raise InputError('Total parameter grid exceeds study budget.',code='resource_limit',status=507)
        accepted=digest(dict(base=data['base'],axis=axis,values=values));summary=dict(axis=axis,values=values,accepted_hash=accepted,total_work=total,
            configurations=[dict(index=i,configuration=c,plan=p,estimate=e) for i,(p,c,_,_,e) in enumerate(rows)])
        if preview:return summary
        if data.get('accepted_hash')!=accepted:raise InputError('Preflight and accept this exact configuration grid before launching.')
        gid=uuid4().hex
        with self.lock:
            if len(self.records)+len(self.pending)+len(rows)>self.limit:raise InputError('Grid exceeds retained study capacity.',code='storage_limit',status=507)
        lease=self.ct.budget.reserve(peak);summary.update(grid_id=gid,status='queued',rows=[dict(index=i,value=v,status='not_started') for i,v in enumerate(values)])
        self.grids[gid]=summary
        def work(job):
            try:
                for i,(p,c,spec,step,e) in enumerate(rows):
                    job.check();sid=uuid4().hex
                    try:
                        if axis=='n_angles':
                            op,_=self.ct.operator(spec,job);step=resolve_step(op,dict(mode='automatic_safe'));p['operator']=op.metadata()
                        result=self.run(sid,p,c,spec,truth,step,job);self.publish(sid,result)
                        summary['rows'][i].update(status=result['status'],study_id=sid,summaries=result['summaries'])
                        if result['status']=='partial_canceled':raise Canceled()
                    except InputError as exc:summary['rows'][i].update(status='failed',reason=str(exc))
                summary['status']='completed'
            except Canceled:summary['status']='partial_canceled'
            job.commit(lambda:dict(grid_id=gid),status='canceled' if summary['status']=='partial_canceled' else 'completed')
        try:return {**self.ct.runner.submit('parameter_study',data['request_id'],summary,work,lease.close),'grid_id':gid}
        except Exception:lease.close();raise
