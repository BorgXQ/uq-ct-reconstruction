"""M4 orchestration on the existing worker, budget and immutable records."""
from copy import deepcopy
from time import perf_counter
from uuid import uuid4
import math
import numpy as np
from threadpoolctl import threadpool_limits
from ..images import InputError
from ..ct.geometry import fields,identifier,integer,scalar
from ..ct.service import Record,digest
from ..ct.jobs import now,Canceled
from ..ct.solver import resolve_step,validate_step,landweber
from ..ct.diagnostics import ImageDiagnostics
from ..exact.numerics import source_config,quantile,resolve_exact_step,covariance,bias_bound,intervals,first_crossing
from ..exact.diagnostics import source_check,diagnose
from .numerics import (Factors,gaussian_factors,probe_policy,evaluate,randomized_crossing,assemble,
                       probability,tsvd_split,ACTION)
from .diagnostics import bias_diagnostics,residual_evidence


class RandomizedService:
    def __init__(self,exact,config):
        self.exact=exact;self.ct=exact.ct
        for table,limit in [('low_rank_factors',32),('randomized_analyses',32),('randomized_views',64),('approximation_diagnostics',32),('stopping_comparisons',16)]:
            self.ct.records[table]={};self.ct.pending[table]=0;self.ct.limits[table]=config.get(table.upper()+'_LIMIT',limit)
        self.max_work=config.get('RANDOMIZED_MAX_WORK',1_000_000_000_000)

    def capture(self,data,exact_id=None,recid=None):
        common=('request_id','rank','oversampling','sketch_seed','probe_seed','probe_count','block_size','epsilon','delta','eta','backend','reference')
        new=('observation_id','selection','source','alpha','step_policy','settings_origin','declaration')
        attached=('iteration','source','alpha','settings_origin','declaration','selection_provenance')
        allowed=common+(('exact_analysis_id',) if exact_id else attached if recid else new)
        fields(data,allowed,allowed);rid=identifier(data['request_id'],'request_id');parent=None;base=None
        config={key:deepcopy(data[key]) for key in common if key!='request_id'}
        if exact_id:
            identifier(exact_id,'exact_analysis_id')
            base=self.ct._get('exact_analyses',exact_id).meta()
            if base['status']!='completed': raise InputError('Select a completed exact analysis.')
            cfg=base['configuration'];recid=base['reconstruction_id'];parent=self.ct._get('reconstructions',recid)
            config.update({key:cfg[key] for key in ('observation_id','source','alpha','settings_origin','declaration')})
            config.update(step_policy=base['step']['step_policy'],selection=dict(mode='attached',iterations=base['iteration'],provenance=base['selection_status']),
                exact_analysis_id=exact_id,reference=True,bound_method=cfg['bound_method'])
        elif recid:
            parent=self.ct._get('reconstructions',recid);rm=parent.meta();cfg=rm['configuration']
            config.update({key:deepcopy(data[key]) for key in ('source','alpha','settings_origin','declaration')})
            config.update(observation_id=rm['observation_id'],step_policy=cfg['step_policy'],
                selection=dict(mode='attached',iterations=integer(data['iteration'],'iteration',0,rm['completed_iterations']),provenance=data['selection_provenance']),bound_method='spectral')
        else:
            config.update({key:deepcopy(data[key]) for key in new});config['bound_method']='spectral'
            selection=config['selection']
            if not isinstance(selection,dict):raise InputError('Expected a selection object.','selection')
            if selection.get('mode')=='automatic':
                fields(selection,('mode','budget'),('mode','budget'))
                integer(selection['budget'],'budget',1,5000);selection['provenance']='randomized_first_crossing'
            elif selection.get('mode')=='fixed':
                fields(selection,('mode','iterations','provenance'),('mode','iterations','provenance'))
                integer(selection['iterations'],'iterations',1,5000)
            else:raise InputError('Choose fixed or automatic selection.')
            if config['step_policy'] not in ('safe_induced_norm_v1','exact_spectral_half','power_iteration_half_v1'):raise InputError('Unsupported fixed-step policy.')
        if parent:
            cfg=parent.meta()['configuration']
            if parent.meta()['status']!='completed' or cfg['initialization']!='zero' or cfg['solver_version']!='landweber_v1' or cfg['dtype']!='float64':
                raise InputError('Requires a completed unmodified Landweber reconstruction.')
        if config['selection']['provenance'] not in ('randomized_first_crossing','exact_first_crossing','fixed_predeclared','retrospective_inspection','imported_selection_unknown','budget_cap_fallback'):
            raise InputError('Unsupported selection provenance.')
        config['source']=source_config(config['source']);quantile(config['alpha'])
        if config['selection']['mode']=='automatic' and config['source'] is None:raise InputError('Automatic stopping requires explicit source parameters.',code='source_missing',status=422)
        if config['settings_origin'] not in ('unverified','declared_independent','retrospective'):raise InputError('Unsupported settings origin.')
        identifier(config['declaration'],'declaration')
        om=self.ct._get('observations',identifier(config['observation_id'],'observation_id')).meta();spec=self.ct.specs[om['operator_id']]
        n=spec.shape[1]
        for key,low,high in [('rank',1,min(512,n)),('oversampling',4,64),('sketch_seed',0,2**32-1),('probe_seed',0,2**32-1),('probe_count',1,1024),('block_size',1,64)]:
            integer(config[key],key,low,high)
        if config['rank']+config['oversampling']>n:raise InputError('Require rank + oversampling ≤ image dimension.')
        for key in ('epsilon','delta'):
            scalar(config[key],key,0,1)
            if not 0<config[key]<1:raise InputError(f'{key} must lie strictly between zero and one.')
        if config['eta'] is not None:
            fields(config['eta'],('value','provenance'),('value','provenance'))
            scalar(config['eta']['value'],'eta');identifier(config['eta']['provenance'],'eta provenance')
        if config['backend'] not in ('matrix_free','full_spectrum_reference'):raise InputError('Unsupported full covariance backend.')
        if type(config['reference']) is not bool:raise InputError('reference must be Boolean.')
        config.update(operator_id=om['operator_id'],observation_hash=om['content_hash'],sigma=om['noise']['sigma'],resolution=spec.resolution,
            reconstruction_id=recid,references=['implementation.md §§5–6','milestone004.md'],algorithm='gaussian_normal_projected_v1')
        acquisition=self.ct._get('acquisitions',om['acquisition_id']).meta()
        return rid,config,parent,spec,acquisition,base

    def preflight(self,spec,c):
        m,n=spec.shape;s=c['selection'];k=s.get('budget',s.get('iterations',0));N=c['probe_count'];ell=c['rank']+c['oversampling'];block=c['block_size']
        # Conservative sparse nonzero ceiling. Work counts scalar multiply-add pairs,
        # plus retained products; reference backend has a separate dense preparation cost.
        # Automatic admission includes the final direct selected-stage cross-check,
        # as well as a full solver run even when a compatible center may be reused.
        columns=(5*k+2)*N if s['mode']=='automatic' else (2*k+1)*N
        normal_columns=2*ell+k+(columns if c['backend']=='matrix_free' and c['sigma'] else 0)
        evaluations=k+1 if s['mode']=='automatic' else 1
        work=normal_columns*m*(2*spec.resolution+1)+2*n*c['rank']*N*evaluations
        if c['backend']=='full_spectrum_reference' and c['sigma']:
            work+=2*n*min(m,n)*N*evaluations
        if work>self.max_work:raise InputError(f'Randomized operator-work estimate {work:,} exceeds {self.max_work:,}. Explicitly reduce acquisition, probes or iteration budget.',code='resource_limit',status=507)
        workspace=spec.estimate_bytes()+8*(8*n*ell+8*ell*ell+8*n*N+4*m*block+180*n)+4096*k+2**20
        mandatory=c['backend']=='full_spectrum_reference' or c['step_policy']=='exact_spectral_half'
        reference=None
        if mandatory or c['reference']:
            try:workspace+=self.exact.preflight(spec,k)
            except InputError as exc:
                if mandatory:raise
                reference=str(exc)
        return workspace,dict(estimated_normal_column_actions=normal_columns,estimated_scalar_work=work,workspace_bytes=workspace,
            reference_preflight_unavailable=reference,action_policy=ACTION if s['mode']=='automatic' else 'matrix_free_direct_v1')

    def reference(self,op,spec,c,job):
        required=c['backend']=='full_spectrum_reference' or c['step_policy']=='exact_spectral_half'
        if not required and not c['reference']:return None,dict(status='unavailable',reason='Not requested; matrix-free path has no full-SVD dependency.',seconds=0)
        try:self.exact.preflight(spec,c['selection'].get('budget',c['selection'].get('iterations',0)))
        except InputError as exc:
            if required:raise
            return None,dict(status='unavailable',reason=str(exc),seconds=0)
        started=perf_counter();sp,hit=self.exact.spectrum(op,job)
        return sp,dict(status='available',spectrum_id=sp.metadata()['spectrum_id'],cache_hit=hit,seconds=perf_counter()-started)

    def step(self,op,sp,c,parent):
        inherited=parent.meta()['configuration'] if parent else None
        if sp:return resolve_exact_step(op,sp,c['step_policy'],inherited)
        if inherited:
            validate_step(op,inherited['omega'])
            return {key:inherited[key] for key in ('omega','step_policy','L_safe','stable_certified','nonoscillatory_certified')}
        return resolve_step(op,{'mode':'automatic_power' if c['step_policy']=='power_iteration_half_v1' else 'automatic_safe'})

    def solver(self,op,c,step,k,acquisition,inference,job,sp):
        cfg=dict(observation_id=c['observation_id'],observation_hash=c['observation_hash'],operator_id=c['operator_id'],iterations=k,**step,
            initialization='zero',dtype='float64',solver_version='landweber_v1',checkpoint_policy='ceil_K_over_100_v1')
        observer=ImageDiagnostics(self.ct.snapshots.array(acquisition['snapshot_id']).ravel())
        def emit(i,x):
            observer.record(i,x)
            if i%5==0 or i==k:job.progress('Reconstructing full-operator selected iterate',i,k)
        result=landweber(op,inference['y'],step['omega'],k,job.event.is_set,emit,step_certificate=sp.certificate() if sp else None)
        rid=uuid4().hex;record=self.ct.reconstruction_record(rid,cfg,acquisition,result,observer.history,False)
        return rid,record,result

    def bundle(self,op,sp,factors,c,step,k,center,policy,job,evaluated=None):
        started=perf_counter()
        if evaluated is None:
            arrays,bound,probe=evaluate(op,factors,step['omega'],c['sigma'],k,policy,c['epsilon'],c['source'],job.check,job.progress,
                sp if c['backend']=='full_spectrum_reference' else None)
        else:
            arrays,bound=evaluated
            probe=dict(completed_probes=policy['count'],backend=ACTION if c['backend']=='matrix_free' else 'full_spectrum_reference',elapsed_seconds=0)
            # A selected-stage independent direct check must agree with the search.
            direct,_,check_meta=evaluate(op,factors,step['omega'],c['sigma'],k,policy,c['epsilon'],c['source'],job.check,job.progress,
                sp if c['backend']=='full_spectrum_reference' else None)
            scale=max(float(np.max(direct['variance'])),np.finfo(float).tiny)
            mismatch=float(np.max(np.abs(direct['signed_residual']-arrays['signed_residual'])))
            if mismatch>1e-9*scale:raise InputError('Search/direct covariance mismatch; selection invalidated.',code='numerical_range',status=422)
            probe.update(direct_check_max_error=mismatch,direct_check_seconds=check_meta['elapsed_seconds'])
        arrays,details=assemble(center,arrays,bound,c['eta'],c['alpha']);details['probe_result']=probe
        probe['deterministic_zero_noise']=c['sigma']==0
        details['retained_covariance_trace']=float(np.sum(arrays['retained_variance']))
        details['corrected_variance_sum']=float(np.sum(arrays['variance']))
        if sp:
            details['full_spectrum']=self.exact._spectrum_summary(sp)
            cov=covariance(sp,step['omega'],k,c['sigma']);full_bound=bias_bound(sp,step,k,c['source'],c['bound_method'])
            full,z=intervals(center,cov,full_bound,c['alpha'])
            arrays.update({f'full_{key}':value for key,value in full.items() if key!='center'})
            arrays['exact_eigenvalues']=sp.eigenvalues
            details['full_bias_allowance']=full_bound;details['full_bias_policy']=c['bound_method']
            evidence,em=residual_evidence(cov['variance'],arrays['retained_variance'],arrays['signed_residual'],arrays['variance'],c['epsilon'])
            arrays.update(evidence);details['residual_reference_check']=em
            try:
                split,summary=tsvd_split(sp,step['omega'],k,c['sigma'],c['source'],c['rank']);arrays.update(split);details['tsvd']=summary
                if summary['combined_bias'] is not None:
                    half=z*cov['pixel_sd']+summary['combined_bias'];arrays.update(tsvd_half_width=half,tsvd_lower=center-half,tsvd_upper=center+half)
            except InputError as exc:
                if exc.code!='tsvd_rank_unavailable':raise
                details['tsvd']=dict(status='unavailable',reason=str(exc))
        arrays['approximate_eigenvalues']=factors.eigenvalues
        details.update(probability(c,factors,k));details['bundle_seconds']=perf_counter()-started
        if sp:
            sm=sp.metadata();rho=None
            if sm['numerical_rank']==op.shape[1] and not sm['ambiguous_modes']:
                rho=float(np.max(np.abs(1-step['omega']*sp.eigenvalues)))
            details['appendix'].update(rho=rho,rho_status='strict_contraction_numerically_checked' if rho is not None and rho<1 else 'rank_deficient_or_unresolved')
        return arrays,details

    def submit(self,data,exact_id=None,recid=None):
        rid,c,parent,spec,acquisition,base=self.capture(deepcopy(data),exact_id,recid)
        workspace,cost=self.preflight(spec,c);aid=uuid4().hex;inference=self.ct.inference(c['observation_id'])
        slots=['low_rank_factors','randomized_analyses']+([] if parent else ['reconstructions'])
        def work(job):
            with threadpool_limits(limits=1,user_api='blas'):
                started=perf_counter();op,_=self.ct.operator(spec,job);sp,reference=self.reference(op,spec,c,job);step=self.step(op,sp,c,parent)
                if c['selection']['mode']=='automatic' and not step['nonoscillatory_certified']:raise InputError('Automatic stopping requires a nonoscillatory certified step.')
                factors=gaussian_factors(op,c['rank'],c['oversampling'],c['sketch_seed'],c['block_size'],job.check,job.progress)
                fm=factors.metadata();fid=uuid4().hex
                policy=probe_policy(op.shape[1],c['probe_count'],c['probe_seed'],c['block_size']);search_start=perf_counter();evaluated=None
                if c['selection']['mode']=='automatic':
                    chosen,evaluated=randomized_crossing(op,factors,step['omega'],c['sigma'],c['source'],policy,c['epsilon'],c['selection']['budget'],job.check,job.progress,
                        sp if c['backend']=='full_spectrum_reference' else None)
                else:chosen=dict(status='fixed',selected_k=c['selection']['iterations'],history=[],last_evaluated_k=None)
                timings=dict(reference=reference['seconds'],sketch=fm['elapsed_seconds'],search=perf_counter()-search_start,landweber=0)
                k=chosen['selected_k'];arrays={};items=[]
                meta=dict(analysis_id=aid,configuration=c,configuration_hash=digest(c),created_at=now(),operator_id=c['operator_id'],
                    observation_id=c['observation_id'],snapshot_id=acquisition['snapshot_id'],shape=[spec.resolution]*2,flatten_order='C',dtype='float64',
                    step=step,selected_k=k,iteration=k,status=chosen['status'],selection={key:v for key,v in chosen.items() if key!='history'},history=chosen['history'],
                    factor_record_id=fid,factors=fm,probe_policy=policy,reference=reference,cost=cost,reconstruction_id=c['reconstruction_id'],
                    intervals_available=False,selection_status=c['selection']['provenance'])
                if k is not None:
                    if parent:center=self.exact.center(parent,k,op,inference,job)
                    else:
                        begin=perf_counter();newrid,record,result=self.solver(op,c,step,k,acquisition,inference,job,sp);timings['landweber']=perf_counter()-begin
                        items.append(('reconstructions',newrid,record));meta['reconstruction_id']=newrid
                        if result['status']=='canceled':meta.update(status='canceled',iteration=None,selected_k=None,completed_iterations=result['completed_iterations']);k=None
                        else:center=result['final']
                    if k is not None:
                        arrays,details=self.bundle(op,sp,factors,c,step,k,center,policy,job,evaluated);meta.update(details,status='completed')
                else:meta.update(probability(c,factors,None))
                meta.update(timings=timings,elapsed_seconds=perf_counter()-started)
                if meta['status']!='canceled':items.append(('low_rank_factors',fid,factors.record))
                else:meta['factor_record_id']=None
                items.append(('randomized_analyses',aid,Record.make(meta,**arrays)))
                def publish():self.ct._publish(items);return dict(analysis_id=aid,reconstruction_id=meta['reconstruction_id'])
                job.commit(publish,'canceled' if meta['status']=='canceled' else 'completed')
        return self.ct._submit('randomized_analysis',rid,dict(analysis_id=aid,configuration=c,cost=cost),slots,workspace,work)

    def response(self,aid,table='randomized_analyses'):
        response=self.ct._get(table,aid).response();response.pop('history',None)
        for key in ('center','variance','lower','upper','total_half_width','full_variance','tsvd_retained_variance','tsvd_omitted_variance'):response.setdefault(key,None)
        return response

    def inspect(self,aid,data):
        fields(data,('request_id','iteration'),('request_id','iteration'));rid=identifier(data['request_id'],'request_id')
        base=self.ct._get('randomized_analyses',aid).meta()
        if base['status']!='completed':raise InputError('No completed center to inspect.')
        parent=self.ct._get('reconstructions',base['reconstruction_id']);k=integer(data['iteration'],'iteration',0,parent.meta()['completed_iterations'])
        c=deepcopy(base['configuration']);c['selection']=dict(mode='attached',iterations=k,provenance=base['selection_status'] if k==base['selected_k'] else 'retrospective_inspection')
        spec=self.ct.specs[c['operator_id']];workspace,_=self.preflight(spec,c);vid=uuid4().hex
        factors=Factors(self.ct._get('low_rank_factors',base['factor_record_id']));inference=self.ct.inference(c['observation_id'])
        def work(job):
            with threadpool_limits(limits=1,user_api='blas'):
                op,_=self.ct.operator(spec,job);sp,ref=self.reference(op,spec,c,job)
                center=self.exact.center(parent,k,op,inference,job)
                arrays,details=self.bundle(op,sp,factors,c,base['step'],k,center,base['probe_policy'],job)
                if base['configuration']['selection']['mode']=='automatic' and k==base['selected_k']:
                    details['probe_control']['iteration_scope']='selected_k_same_probes'
                    details['unmet_conditions'].append('This K was selected using the same probes; fixed-k control is insufficient.')
                elif k!=base['selected_k']:
                    details['probe_control']['iteration_scope']='retrospective_inspected_k'
                meta={**base,**details,'view_id':vid,'iteration':k,'reference':ref,'created_at':now()};meta.pop('history',None)
                record=Record.make(meta,**arrays)
                job.commit(lambda:self.exact._publish_one('randomized_views',vid,record,dict(view_id=vid,analysis_id=aid)))
        return self.ct._submit('randomized_inspection',rid,dict(analysis_id=aid,iteration=k),['randomized_views'],workspace,work)

    def diagnostics(self,aid,data):
        fields(data,('request_id','view_id'),('request_id',));rid=identifier(data['request_id'],'request_id')
        record=self.ct._get('randomized_views',data['view_id']) if data.get('view_id') else self.ct._get('randomized_analyses',aid)
        meta=record.meta()
        if meta['analysis_id']!=aid or meta['status']!='completed':raise InputError('Diagnostics require the matching completed analysis/view.')
        c=meta['configuration'];k=meta['iteration'];spec=self.ct.specs[c['operator_id']];factors=Factors(self.ct._get('low_rank_factors',meta['factor_record_id']))
        truth=self.ct.snapshots.array(meta['snapshot_id']).ravel();did=uuid4().hex
        workspace,_=self.preflight(spec,{**c,'selection':dict(mode='fixed',iterations=k)})
        def work(job):
            with threadpool_limits(limits=1,user_api='blas'):
                started=perf_counter();op,_=self.ct.operator(spec,job);sp,ref=self.reference(op,spec,c,job)
                job.progress('Checking omitted bias on frozen truth',0,k)
                bound=meta['retained_bias_allowance'];eta=c['eta']
                parts,bm=bias_diagnostics(op,factors,meta['step']['omega'],k,truth,bound['value'] if bound else None,eta['value'] if eta else None,job.check)
                total=meta['total_bias_allowance'];available=meta['intervals_available']
                arrays,metrics=diagnose(record.array('center'),truth+parts['actual_bias'],truth,total['value'] if total else None,
                    record.array('lower') if available else None,record.array('upper') if available else None)
                arrays.update(parts);source=source_check(sp,truth,c['source']) if sp else dict(status='unavailable',reason='Full source inversion unavailable without exact reference.')
                discrepancy=dict(status='unavailable',reason='Dense discrepancy diagnostic capped at 256 pixels or exact reference unavailable.')
                if sp and op.shape[1]<=256:
                    v=factors.vectors;normal=sp.vectors@(sp.eigenvalues[:,None]*sp.vectors.T)-v@(factors.eigenvalues[:,None]*v.T)
                    from ..exact.numerics import filters
                    w=c['sigma']**2*filters(sp.eigenvalues,meta['step']['omega'],k)[1]
                    wr=c['sigma']**2*filters(factors.eigenvalues,meta['step']['omega'],k)[1]
                    cov=sp.vectors@(w[:,None]*sp.vectors.T)-v@(wr[:,None]*v.T)
                    discrepancy=dict(status='observed_numerical_discrepancy',normal_operator_norm=float(np.max(np.abs(np.linalg.eigvalsh(normal)))),
                        covariance_norm=float(np.max(np.abs(np.linalg.eigvalsh(cov)))),beta=None)
                failed=source['status']=='violated' or bm['retained_assumption_supported'] is False or bm['omitted_assumption_supported'] is False
                dm=dict(diagnostic_id=did,analysis_id=aid,view_id=meta.get('view_id'),iteration=k,factor_id=factors.metadata()['factor_id'],
                    configuration_hash=meta['configuration_hash'],operator_id=meta['operator_id'],observation_id=meta['observation_id'],
                    snapshot_id=meta['snapshot_id'],shape=meta['shape'],created_at=now(),source_check=source,**metrics,**{key:value for key,value in bm.items() if key not in metrics},
                    discrepancy=discrepancy,effective_interpretation='Exploratory — a declared bias/source assumption is contradicted.' if failed else 'Exploratory — scoped diagnostics do not establish probability control.',elapsed_seconds=perf_counter()-started)
                job.check();out=Record.make(dm,**arrays)
                job.commit(lambda:self.exact._publish_one('approximation_diagnostics',did,out,dict(diagnostic_id=did,analysis_id=aid)))
        return self.ct._submit('randomized_diagnostics',rid,dict(analysis_id=aid,iteration=k),['approximation_diagnostics'],workspace,work)

    def own_stopping(self,aid,data):
        data=deepcopy(data)
        fields(data,('request_id','budget','c0','bound_method'),('request_id','budget','c0','bound_method'))
        rid=identifier(data['request_id'],'request_id');budget=integer(data['budget'],'budget',1,5000);c0=scalar(data['c0'],'c0')
        if c0<=0:raise InputError('c0 must be positive.')
        base=self.ct._get('randomized_analyses',aid).meta();c=deepcopy(base['configuration'])
        if not base.get('factor_record_id') or c['source'] is None:raise InputError('Completed factors and source are required.')
        c.update(reference=True,selection=dict(mode='automatic',budget=budget,provenance='randomized_first_crossing'))
        spec=self.ct.specs[c['operator_id']];self.exact.preflight(spec,budget)
        workspace,_=self.preflight(spec,c)
        factors=Factors(self.ct._get('low_rank_factors',base['factor_record_id']));cid=uuid4().hex;inference=self.ct.inference(c['observation_id'])
        def work(job):
            with threadpool_limits(limits=1,user_api='blas'):
                op,_=self.ct.operator(spec,job);sp,_=self.exact.spectrum(op,job);step=base['step']
                start=perf_counter()
                exact=first_crossing(sp,step,c['sigma'],c['source'],data['bound_method'],c0,budget,job.check,job.progress)
                exact_seconds=perf_counter()-start;start=perf_counter()
                rand,evaluated=randomized_crossing(op,factors,step['omega'],c['sigma'],c['source'],base['probe_policy'],c['epsilon'],budget,job.check,job.progress,
                    sp if c['backend']=='full_spectrum_reference' else None)
                randomized_seconds=perf_counter()-start;arrays={};summaries={};solver_seconds={};bundle_seconds={}
                for name,chosen in [('exact',exact),('randomized',rand)]:
                    k=chosen['selected_k'];summaries[name]=chosen
                    if k is None:continue
                    start=perf_counter();result=landweber(op,inference['y'],step['omega'],k,job.event.is_set,step_certificate=sp.certificate());job.check()
                    solver_seconds[name]=perf_counter()-start;start=perf_counter()
                    if name=='exact':
                        cov=covariance(sp,step['omega'],k,c['sigma']);b=bias_bound(sp,step,k,c['source'],data['bound_method']);a,z=intervals(result['final'],cov,b,c['alpha'])
                    else:a,_=self.bundle(op,sp,factors,c,step,k,result['final'],base['probe_policy'],job,evaluated)
                    bundle_seconds[name]=perf_counter()-start
                    arrays.update({name+'_'+key:value for key,value in a.items() if key in ('center','lower','upper','total_half_width','noise_half_width')})
                meta=dict(comparison_id=cid,analysis_id=aid,observation_id=c['observation_id'],operator_id=c['operator_id'],step=step,
                    exact_bound_method=data['bound_method'],exact_c0=c0,randomized_balance=1,source=c['source'],shape=base['shape'],
                    selections=summaries,configuration=c,created_at=now(),timings=dict(exact_search=exact_seconds,randomized_search=randomized_seconds,
                        landweber=solver_seconds,interval_bundles=bundle_seconds),
                    interpretation='Own stopping points: different K and centers. Randomized probability control remains unverified.')
                out=Record.make(meta,**arrays);job.commit(lambda:self.exact._publish_one('stopping_comparisons',cid,out,dict(comparison_id=cid)))
        return self.ct._submit('randomized_stopping_comparison',rid,dict(analysis_id=aid,budget=budget),['stopping_comparisons'],workspace,work)
