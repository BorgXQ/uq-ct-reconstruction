"""Exact plans on M2's single worker and immutable record/resource infrastructure."""
from collections import OrderedDict
from copy import deepcopy
from time import perf_counter
from uuid import uuid4
import numpy as np
from ..images import InputError
from ..ct.geometry import fields, identifier, integer, scalar
from ..ct.service import Record, digest
from ..ct.solver import landweber, replay
from ..ct.diagnostics import ImageDiagnostics
from ..ct.jobs import now
from .spectrum import decompose, workspace_bytes, VERSION, QUALITY_TOL
from .numerics import (source_config,quantile,resolve_exact_step,compatible_bound,covariance,bias_bound,
                       first_crossing,intervals,joint_status)
from .diagnostics import diagnose,source_check


class ExactService:
    def __init__(self, ct, config):
        self.ct=ct
        for table,limit in [('exact_analyses',32),('exact_views',64),('exact_diagnostics',32)]:
            ct.records[table]={};ct.pending[table]=0;ct.limits[table]=config.get(table.upper()+'_LIMIT',limit)
        self.cache=OrderedDict();self.cache_bytes=0
        self.cache_limit=config.get('EXACT_SPECTRUM_CACHE_BYTES',64*1024**2)
        self.max_elements=config.get('EXACT_MAX_DENSE_ELEMENTS',8_000_000)
        self.max_search_work=config.get('EXACT_MAX_SEARCH_WORK',6_000_000_000)

    def preflight(self,spec,budget):
        m,n=spec.shape
        if spec.resolution not in (16,32):
            raise InputError('Full-spectrum baseline supports 16×16 and 32×32. This observation remains usable in M2; no downsampling was performed.',code='unsupported_exact_size',status=422)
        if m*n>self.max_elements or n*min(m,n)*budget>self.max_search_work:
            raise InputError('Exact dense/work limit exceeded; select a smaller acquisition explicitly.',code='resource_limit',status=507)
        return workspace_bytes(m,n,budget)+spec.estimate_bytes()

    def spectrum(self,op,job):
        key=(op.metadata()['operator_id'],op.metadata()['csr_checksum'],VERSION,QUALITY_TOL)
        with self.ct.lock:
            if key in self.cache:
                spectrum,lease=self.cache.pop(key);self.cache[key]=(spectrum,lease);return spectrum,True
        spectrum=decompose(op,job.check,job.progress);job.check();size=spectrum.size()
        with self.ct.lock:
            while self.cache and self.cache_bytes+size>self.cache_limit:
                _,(old,lease)=self.cache.popitem(last=False);self.cache_bytes-=old.size();lease.close()
            if size<=self.cache_limit:
                lease=self.ct.budget.reserve(size);self.cache[key]=(spectrum,lease);self.cache_bytes+=size
        return spectrum,False

    def _spectrum_summary(self,spectrum):
        return {k:v for k,v in spectrum.metadata().items() if k not in ('singular_values','eigenvalues')}

    def _config(self,data,recid=None):
        common=('request_id','source','bound_method','alpha','settings_origin','declaration')
        allowed=common+(('iteration','selection_provenance') if recid else ('observation_id','step_policy','selection'))
        fields(data,allowed,allowed)
        rid=identifier(data['request_id'],'request_id')
        source=source_config(data['source']);alpha=scalar(data['alpha'],'alpha',0,1);quantile(alpha)
        if data['settings_origin'] not in ('unverified','declared_independent','retrospective'):
            raise InputError('Unsupported settings origin.','settings_origin')
        declaration=identifier(data['declaration'],'declaration')
        parent=None
        if recid:
            parent=self.ct._get('reconstructions',recid);rm=parent.meta();cfg=rm['configuration']
            if rm['status']!='completed' or cfg['initialization']!='zero' or cfg['solver_version']!='landweber_v1' or cfg['dtype']!='float64':
                raise InputError('Requires a completed, unmodified float64 Landweber run.',code='incompatible_reconstruction',status=422)
            k=integer(data['iteration'],'iteration',0,rm['completed_iterations'])
            provenance=data['selection_provenance']
            if provenance not in ('fixed_predeclared','retrospective_inspection','imported_selection_unknown'):
                raise InputError('Unsupported selection provenance.','selection_provenance')
            oid=rm['observation_id'];policy=cfg['step_policy']
            selection=dict(mode='attached',iterations=k,provenance=provenance)
        else:
            oid=identifier(data['observation_id'],'observation_id');policy=data['step_policy']
            if policy not in ('exact_spectral_half','safe_induced_norm_v1','power_iteration_half_v1'): raise InputError('Unsupported step policy.','step_policy')
            sel=data['selection']
            if not isinstance(sel,dict): raise InputError('Expected selection object.')
            if sel.get('mode')=='fixed':
                fields(sel,('mode','iterations','provenance'),('mode','iterations','provenance'))
                if sel['provenance'] not in ('fixed_predeclared','retrospective_inspection','imported_selection_unknown'):
                    raise InputError('Unsupported selection provenance.')
                selection=dict(mode='fixed',iterations=integer(sel['iterations'],'iterations',1,5000),provenance=sel['provenance'])
            elif sel.get('mode')=='automatic':
                fields(sel,('mode','budget','c0'),('mode','budget','c0'))
                c0=scalar(sel['c0'],'c0')
                if c0<=0: raise InputError('c0 must be positive.','c0')
                if source is None: raise InputError('Automatic stopping requires source parameters.',code='source_missing',status=422)
                selection=dict(mode='automatic',budget=integer(sel['budget'],'budget',1,5000),c0=c0,provenance='exact_first_crossing')
            else: raise InputError('Choose fixed or automatic selection.','mode')
        observation=self.ct._get('observations',oid).meta();spec=self.ct.specs[observation['operator_id']]
        acquisition=self.ct._get('acquisitions',observation['acquisition_id']).meta()
        compatible_bound(data['bound_method'],{'step_policy':policy})
        config=dict(observation_id=oid,observation_hash=observation['content_hash'],operator_id=observation['operator_id'],
            sigma=observation['noise']['sigma'],resolution=spec.resolution,step_policy=policy,selection=selection,
            source=source,bound_method=data['bound_method'],alpha=alpha,settings_origin=data['settings_origin'],
            declaration=declaration,reconstruction_id=recid,
            numerical_policy='full_svd_stable_filters_v1',references=['implementation.md §§2–4','prod.md §6','milestone003.md'])
        return rid,config,parent,spec,acquisition

    def submit(self,data,recid=None,cap_parent=None):
        rid,config,parent,spec,acquisition=self._config(deepcopy(data),recid)
        if cap_parent:
            config['selection']['provenance']='budget_cap_fallback';config['cap_parent_analysis_id']=cap_parent
        selection=config['selection'];budget=selection.get('budget',selection.get('iterations',0))
        workspace=self.preflight(spec,budget)
        aid=uuid4().hex;hash_id=digest(config)
        inference=self.ct.inference(config['observation_id'])
        slots=['exact_analyses']+([] if parent else ['reconstructions'])
        def work(job):
            started=perf_counter();op,op_hit=self.ct.operator(spec,job);spectrum,hit=self.spectrum(op,job)
            step=resolve_exact_step(op,spectrum,config['step_policy'],parent.meta()['configuration'] if parent else None)
            if selection['mode']=='automatic':
                chosen=first_crossing(spectrum,step,config['sigma'],config['source'],config['bound_method'],
                    selection['c0'],selection['budget'],job.check,job.progress)
            else:
                chosen=dict(status='fixed',selected_k=selection['iterations'],history=[],last_evaluated_k=None)
            meta=dict(analysis_id=aid,plan_id=aid,configuration_hash=hash_id,configuration=config,
                created_at=now(),observation_id=config['observation_id'],operator_id=config['operator_id'],
                snapshot_id=acquisition['snapshot_id'],snapshot_content_hash=acquisition['snapshot_content_hash'],
                shape=[spec.resolution]*2,flatten_order='C',dtype='float64',step=step,
                spectrum=self._spectrum_summary(spectrum),spectrum_cache_hit=hit,
                selection={k:v for k,v in chosen.items() if k!='history'},history=chosen['history'],
                selection_status=selection['provenance'],selected_k=chosen['selected_k'],reconstruction_id=recid,
                status=chosen['status'])
            items=[];arrays={};k=chosen['selected_k']
            if k is not None:
                if parent:
                    center=self.center(parent,k,op,inference,job)
                else:
                    rconfig=dict(observation_id=inference['observation_id'],observation_hash=config['observation_hash'],
                        operator_id=spec.geometry_hash,iterations=k,**step,initialization='zero',dtype='float64',
                        solver_version='landweber_v1',checkpoint_policy='ceil_K_over_100_v1')
                    # Validation observer cannot control inference or selection; preserves M2 history views.
                    diagnostics=ImageDiagnostics(self.ct.snapshots.array(acquisition['snapshot_id']).ravel())
                    def emit(iteration,x):
                        diagnostics.record(iteration,x)
                        if iteration%5==0 or iteration==k: job.progress('Reconstructing selected iterate',iteration,k)
                    result=landweber(op,inference['y'],step['omega'],k,job.event.is_set,emit,step_certificate=spectrum.certificate())
                    newrid=uuid4().hex
                    record=self.ct.reconstruction_record(newrid,rconfig,acquisition,result,diagnostics.history,op_hit)
                    items.append(('reconstructions',newrid,record));meta['reconstruction_id']=newrid
                    if result['status']=='canceled':
                        meta['status']='canceled';meta['completed_iterations']=result['completed_iterations'];k=None
                    else: center=result['final']
                if k is not None:
                    job.check();job.progress('Evaluating covariance',k,k)
                    arrays,details=self.bundle(spectrum,step,config,k,center,selection['provenance'])
                    meta.update(details,status='completed',iteration=k)
            if k is None:
                meta.update(iteration=None,intervals_available=False,intervals_unavailable_reason=meta['status'])
            meta['elapsed_seconds']=perf_counter()-started
            record=Record.make(meta,**arrays);items.append(('exact_analyses',aid,record))
            def publish():
                self.ct._publish(items)
                return dict(analysis_id=aid,reconstruction_id=meta['reconstruction_id'])
            job.commit(publish,'canceled' if meta['status']=='canceled' else 'completed')
        return self.ct._submit('exact_analysis',rid,dict(analysis_id=aid,configuration_hash=hash_id,configuration=config),slots,workspace,work)

    def center(self,record,k,op,inference,job):
        m=record.meta();cfg=m['configuration']
        if cfg['operator_id']!=op.metadata()['operator_id'] or cfg['observation_id']!=inference['observation_id']:
            raise InputError('Checkpoint inputs do not match the analysis.',code='incompatible_reconstruction',status=422)
        if k in m['checkpoint_indices']: return record.array('checkpoint_'+str(k))
        start=max(i for i in m['checkpoint_indices'] if i<=k)
        return replay(op,inference['y'],cfg['omega'],record.array('checkpoint_'+str(start)),start,k,job.check,job.progress)

    def bundle(self,spectrum,step,config,k,center,provenance):
        cov=covariance(spectrum,step['omega'],k,config['sigma'])
        bound=bias_bound(spectrum,step,k,config['source'],config['bound_method'])
        arrays,z=intervals(center,cov,bound,config['alpha'])
        source_status='not_supplied' if config['source'] is None else 'assumed_unverified'
        unmet=[]
        if config['source'] is None: unmet.append('Source parameters not supplied; total-error intervals unavailable.')
        else: unmet.append('Assumed source condition — not verified for this drawing.')
        independent=provenance in ('fixed_predeclared','exact_first_crossing') and config['settings_origin']=='declared_independent'
        if not independent: unmet.append('Noise independence of chosen settings/iteration is not established; exploratory interpretation.')
        if k==0: unmet.append('Initialization: no total-error interval analysis.')
        warnings=spectrum.metadata()['warnings']+cov['warnings']
        label='Stochastic variability only' if bound is None else 'Conditional interval formula' if independent else 'Exploratory interval'
        conditions=[dict(name='model_and_solver',scope='captured discrete simulation/run',status='verified_by_construction',
                        reason='Known additive isotropic Gaussian noise; fixed operator, zero initialization, unmodified linear Landweber.'),
                    dict(name='source_condition',scope=config['operator_id'],status=source_status,declared=config['source'],
                        reason='An explicit declaration is not evidence that an arbitrary drawing belongs to this source class.'),
                    dict(name='selection_independence',scope=provenance,status='assumed_unverified' if independent else 'unavailable',
                        reason=config['declaration'])]
        return arrays,dict(trace_covariance=cov['trace'],max_pixel_sd=cov['max_pixel_sd'],bias_allowance=bound,
            gaussian_quantile=z,nominal_gaussian_level=1-config['alpha'],source_condition_status=source_status,
            selection_status=provenance,numerical_status='warnings' if warnings else 'checked',warnings=warnings,
            coverage_interpretation=label,unmet_conditions=unmet,conditions=conditions,
            intervals_available=bound is not None,intervals_unavailable_reason=None if bound is not None else 'initialization' if k==0 else 'source_missing',
            joint_region=joint_status(spectrum,cov,config['sigma'],k))

    def response(self,aid,table='exact_analyses'):
        record=self.ct._get(table,aid);data=record.response();data.pop('history',None)
        for key in ('center','variance','pixel_sd','noise_half_width','total_half_width','lower','upper'):
            data.setdefault(key,None)
        return data

    def inspect(self,aid,data):
        fields(data,('request_id','iteration'),('request_id','iteration'));rid=identifier(data['request_id'],'request_id')
        base=self.ct._get('exact_analyses',aid).meta()
        if base['status']!='completed': raise InputError('No completed center to inspect.',code='incompatible_reconstruction',status=422)
        parent=self.ct._get('reconstructions',base['reconstruction_id']);k=integer(data['iteration'],'iteration',0,parent.meta()['completed_iterations'])
        config=base['configuration'];spec=self.ct.specs[base['operator_id']];vid=uuid4().hex
        inference=self.ct.inference(base['observation_id'])
        def work(job):
            op,_=self.ct.operator(spec,job);spectrum,_=self.spectrum(op,job)
            center=self.center(parent,k,op,inference,job);job.check()
            provenance=base['selection_status'] if k==base['selected_k'] else 'retrospective_inspection'
            arrays,details=self.bundle(spectrum,base['step'],config,k,center,provenance)
            meta={key:value for key,value in base.items() if key!='history'}
            meta.update(details,view_id=vid,analysis_id=aid,iteration=k,created_at=now(),status='completed')
            record=Record.make(meta,**arrays)
            job.commit(lambda:self._publish_one('exact_views',vid,record,dict(view_id=vid,analysis_id=aid,iteration=k)))
        return self.ct._submit('exact_inspection',rid,dict(analysis_id=aid,iteration=k),['exact_views'],self.preflight(spec,0),work)

    def _publish_one(self,table,key,record,result):
        self.ct._publish([(table,key,record)]);return result

    def diagnostics(self,aid,data):
        fields(data,('request_id',),('request_id',));rid=identifier(data['request_id'],'request_id')
        analysis=self.ct._get('exact_analyses',aid);meta=analysis.meta()
        if meta['status']!='completed': raise InputError('Diagnostics require a completed analysis.',code='diagnostics_unavailable',status=422)
        config=meta['configuration'];spec=self.ct.specs[meta['operator_id']];did=uuid4().hex
        # Private truth resolution belongs only to this explicitly requested diagnostic action.
        observation=self.ct._get('observations',meta['observation_id']).meta()
        acquisition=self.ct._get('acquisitions',observation['acquisition_id'])
        truth=self.ct.snapshots.array(acquisition.meta()['snapshot_id']).ravel();clean=acquisition.array('y_clean')
        def work(job):
            started=perf_counter();op,_=self.ct.operator(spec,job);spectrum,_=self.spectrum(op,job)
            job.progress('Checking synthetic truth',0,None);k=meta['iteration']
            if k==0: noiseless=np.zeros_like(truth)
            else:
                result=landweber(op,clean,meta['step']['omega'],k,job.event.is_set,
                    lambda i,x:job.progress('Checking synthetic truth — noiseless reconstruction',i,k),step_certificate=spectrum.certificate())
                job.check();noiseless=result['final']
            bound=meta['bias_allowance']['value'] if meta['bias_allowance'] else None
            arrays,metrics=diagnose(analysis.array('center'),noiseless,truth,bound,
                analysis.array('lower') if meta['intervals_available'] else None,
                analysis.array('upper') if meta['intervals_available'] else None)
            evidence=source_check(spectrum,truth,config['source']);job.check()
            dmeta=dict(diagnostic_id=did,analysis_id=aid,configuration_hash=meta['configuration_hash'],
                snapshot_id=meta['snapshot_id'],operator_id=meta['operator_id'],observation_id=meta['observation_id'],
                reconstruction_id=meta['reconstruction_id'],iteration=k,shape=meta['shape'],flatten_order='C',dtype='float64',
                created_at=now(),source_check=evidence,**metrics,elapsed_seconds=perf_counter()-started)
            if evidence['status']=='violated' or metrics['bias_bound_supported'] is False:
                dmeta['effective_interpretation']='Exploratory — source or bias allowance contradicted; no applicable coverage claim.'
            else: dmeta['effective_interpretation']=meta['coverage_interpretation']+'; diagnostic evidence is scoped to this frozen image.'
            record=Record.make(dmeta,**arrays)
            job.commit(lambda:self._publish_one('exact_diagnostics',did,record,dict(diagnostic_id=did,analysis_id=aid)))
        return self.ct._submit('exact_diagnostics',rid,dict(analysis_id=aid),['exact_diagnostics'],self.preflight(spec,meta['iteration']),work)

    def cap(self,aid,data):
        fields(data,('request_id',),('request_id',))
        meta=self.ct._get('exact_analyses',aid).meta();c=meta['configuration']
        if meta['status']!='criterion_not_met': raise InputError('Cap fallback requires an unmet search.')
        request={k:c[k] for k in ('source','bound_method','alpha','step_policy','observation_id')}
        request.update(request_id=data['request_id'],settings_origin='retrospective',declaration='Explicit budget-cap fallback after unmet criterion.',
            selection=dict(mode='fixed',iterations=c['selection']['budget'],provenance='retrospective_inspection'))
        return self.submit(request,cap_parent=aid)
