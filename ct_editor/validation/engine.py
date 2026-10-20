"""Batch adapter: inference preparation receives no truth or clean observation."""
from copy import deepcopy
from time import perf_counter
import numpy as np
from ..images import InputError
from ..ct.jobs import Canceled
from ..ct.solver import landweber
from ..exact.numerics import covariance,bias_bound,first_crossing,intervals,quantile
from ..exact.diagnostics import source_check
from ..randomized.numerics import (gaussian_factors,probe_policy,evaluate,randomized_crossing,assemble,
                                  tsvd_split,probability)
from ..randomized.diagnostics import bias_diagnostics,residual_evidence
from .statistics import Statistics

METHODS=('exact','tsvd','retained','corrected')
SEED_VERSION='validation_seedsequence_v1'


def seed_words(root,config,trial,role):
    # 128-bit child entropy; role/config/trial independent of work ordering.
    return np.random.SeedSequence(root,spawn_key=(5,config,trial,role)).generate_state(4).tolist()


def observation(clean,sigma,root,config,trial):
    return clean+sigma*np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed_words(root,config,trial,0)))).standard_normal(len(clean))


def solve(op,y,step,k,job,sp=None):
    job.check()
    result=landweber(op,y,step['omega'],k,lambda:job.event.is_set(),
        step_certificate=sp.certificate() if sp else None,retain_history=False)
    job.check()
    return result['final']


def prepare(op,sp,c,plan,step,trial,job,restored_factors=None):
    """No observation values or truth; same M3/M4 equations and first crossings."""
    began=perf_counter();contexts={};f=None;policy=None;random_failure=None
    use_random=any(m in plan['methods'] for m in ('retained','corrected'))
    cfg=deepcopy(c);fresh=plan['protocol']=='all_random_sources'
    seeds=dict(sketch=c.get('sketch_seed'),probe=c.get('probe_seed'))
    if fresh:
        seeds=dict(sketch=seed_words(plan['root_seed'],plan['configuration_index'],trial,1),
                   probe=seed_words(plan['root_seed'],plan['configuration_index'],trial,2))
    if use_random:
        try:
            # M4 accepts a scalar sketch seed. Its column generator also supports
            # SeedSequence entropy lists; direct factors use that same role policy.
            f=restored_factors if not fresh and restored_factors is not None else gaussian_factors(
                op,cfg['rank'],cfg['oversampling'],0 if fresh else seeds['sketch'],cfg['block_size'],job.check,job.progress, seed_entropy=seeds['sketch'] if fresh else None)
            policy=probe_policy(op.shape[1],cfg['probe_count'],seeds['probe'],cfg['block_size'])
            cfg.update(sketch_seed=seeds['sketch'],probe_seed=seeds['probe'])
        except InputError as exc:random_failure=dict(status='rank_failure' if exc.code=='sample_rank_deficient' else 'numerical_failure',reason=str(exc))
    exact_selection=None;random_selection=None;random_arrays=None;random_bound=None;probe=None
    if plan['comparison']=='own_stopping':
        if any(m in plan['methods'] for m in ('exact','tsvd')) and sp is not None:
            exact_selection=first_crossing(sp,step,c['sigma'],c['source'],c['bound_method'],plan['c0'],plan['budget'],job.check,job.progress)
        if use_random and not random_failure:
            random_selection,evaluated=randomized_crossing(op,f,step['omega'],c['sigma'],c['source'],policy,c['epsilon'],plan['budget'],job.check,job.progress,
                sp if c['backend']=='full_spectrum_reference' else None)
            if evaluated is not None:random_arrays,random_bound=evaluated
    for method in plan['methods']:
        job.check()
        if method in ('exact','tsvd') and sp is None:
            contexts[method]=dict(status='reference_unavailable',reason=plan.get('reference_reason','Exact reference unavailable'));continue
        if method in ('retained','corrected') and random_failure:
            contexts[method]=random_failure.copy();continue
        selection=(exact_selection if method in ('exact','tsvd') else random_selection) if plan['comparison']=='own_stopping' else dict(status='fixed',selected_k=plan['iterations'])
        if selection['selected_k'] is None:
            contexts[method]=dict(status=selection['status'],reason='No selected K; strict selection without cap fallback',selection=selection);continue
        k=selection['selected_k'];omega=step['omega'];half=None;allowance=None;arrays={};meta={}
        try:
            if method in ('exact','tsvd'):
                cov=covariance(sp,omega,k,c['sigma']);b=bias_bound(sp,step,k,c['source'],c['bound_method'])
                if method=='tsvd':
                    split,split_meta=tsvd_split(sp,omega,k,c['sigma'],c['source'],c['rank']);arrays.update(split)
                    if b is not None:b={**b,'value':split_meta['combined_bias'],'method':'exact_tsvd_split'}
                    meta['split']=split_meta
                arrays.update(cov);allowance=None if b is None else b['value']
                a,_=intervals(np.zeros(op.shape[1]),cov,b,c['alpha']);half=a.get('total_half_width')
                meta['interpretation']='Full-model formula conditional on source assumption and noise-independent settings'
            else:
                if random_arrays is None:
                    random_arrays,random_bound,probe=evaluate(op,f,omega,c['sigma'],k,policy,c['epsilon'],c['source'],job.check,job.progress,
                        sp if c['backend']=='full_spectrum_reference' else None)
                elif random_selection is not None and probe is None:
                    direct,_,probe=evaluate(op,f,omega,c['sigma'],k,policy,c['epsilon'],c['source'],job.check,job.progress,
                        sp if c['backend']=='full_spectrum_reference' else None)
                    if not np.allclose(direct['variance'],random_arrays['variance'],rtol=1e-9,atol=1e-14):
                        raise InputError('Search/direct covariance mismatch.',code='numerical_range')
                arrays.update(random_arrays)
                if method=='retained':
                    cov=covariance(f,omega,k,c['sigma']);a,_=intervals(np.zeros(op.shape[1]),cov,random_bound,c['alpha'])
                    half=a.get('total_half_width');allowance=None if random_bound is None else random_bound['value']
                    meta['interpretation']='Retained-only diagnostic; omitted error excluded; no full-error coverage guarantee'
                else:
                    a,info=assemble(np.zeros(op.shape[1]),random_arrays,random_bound,c['eta'],c['alpha']);half=a.get('total_half_width')
                    allowance=info['total_bias_allowance']['value'] if info['total_bias_allowance'] else None
                    meta['interpretation']='Exploratory corrected formula; probability conditions not established'
                probcfg={**cfg,'selection':{'mode':'automatic' if plan['comparison']=='own_stopping' else 'fixed','provenance':'randomized_first_crossing' if plan['comparison']=='own_stopping' else 'fixed_predeclared'}}
                meta['probability']=probability(probcfg,f,k);meta['probe_work']=probe
            if plan['scope']=='moments':half=None
            contexts[method]=dict(status='ready',k=k,half=half,bias_allowance=allowance,arrays=arrays,metadata=meta,
                selection={key:v for key,v in selection.items() if key!='history'})
        except InputError as exc:
            contexts[method]=dict(status='numerical_failure',reason=str(exc))
    return contexts,f,policy,seeds,perf_counter()-began


def safe_prepare(*args,**kwargs):
    try:return prepare(*args,**kwargs)
    except InputError as exc:
        c,plan=args[2],args[3]
        status='resource_failure' if exc.code in ('resource_limit','storage_limit') else 'numerical_failure'
        return ({m:dict(status=status,reason=str(exc)) for m in plan['methods']},None,None,
                dict(sketch=c.get('sketch_seed'),probe=c.get('probe_seed')),0.)


def experiment(op,sp,truth,plan,c,step,job,restored_factors=None,stored_observations=None,indices=None):
    start=perf_counter();clean=op.forward(truth);n=len(truth);R=plan['trials'];methods=plan['methods']
    stats={m:Statistics(n) for m in methods};ledger=[];series={m:{str(i):[] for i in plan['pixels']} for m in methods}
    representatives={};references={};fixed_payload={};pairs={};bias_curve=[];diagnostic_counts={m:dict(checked=0,violated=0,unavailable=0,skipped=0) for m in methods}
    prep_seconds=0.;reconstruction_seconds=0.;diagnostic_seconds=0.;aggregation_seconds=0.;fixed=None;f=None;policy=None;seeds=None
    try:source=source_check(sp,truth,c['source']) if sp else dict(status='unavailable',reason='Exact spectrum unavailable')
    except InputError as exc:source=dict(status='unavailable',reason=str(exc))
    stop='completed';attempted=0;current_trial=None;selected_indices=list(range(R)) if indices is None else list(indices)
    try:
        if plan['protocol']=='fixed_algorithm':
            fixed,f,policy,seeds,t=safe_prepare(op,sp,c,plan,step,0,job,restored_factors);prep_seconds+=t
            if f is not None:fixed_payload=dict(values=f.eigenvalues.copy(),vectors=f.vectors.copy(),metadata=f.metadata(),probe_policy=policy)
        mean_cache={}
        for trial in selected_indices:
            job.check();current_trial=trial;job.progress('Validation trial',attempted,len(selected_indices))
            if fixed is None:
                contexts,f,policy,seeds,t=safe_prepare(op,sp,c,plan,step,trial,job);prep_seconds+=t
            else:contexts=fixed
            y=stored_observations[trial] if stored_observations is not None and trial in stored_observations else observation(clean,c['sigma'],plan['root_seed'],plan['configuration_index'],trial)
            centers={};trial_results={};pending=[];trial_start=perf_counter()
            for method in methods:
                job.check();ctx=contexts[method];row=dict(trial_index=trial,method=method,attempt=1,status=ctx['status'],k=ctx.get('k'),
                    measurement_entropy=seed_words(plan['root_seed'],plan['configuration_index'],trial,0),sketch_seed=seeds['sketch'],probe_seed=seeds['probe'],
                    factor_id=f.metadata()['factor_id'] if f is not None else None,probe_id=policy['probe_id'] if policy else None,
                    diagnostic_status='not_checked',reason=ctx.get('reason'),metadata=ctx.get('metadata',{}),selection=ctx.get('selection'))
                x=None;h=None;diag=None
                if ctx['status']=='ready':
                    k=ctx['k'];t=perf_counter()
                    try:
                        if k not in centers:centers[k]=solve(op,y,step,k,job,sp)
                    except InputError as exc:
                        row.update(status='numerical_failure',reason=str(exc));pending.append((method,row,None,None));continue
                    reconstruction_seconds+=perf_counter()-t;x=centers[k];h=ctx['half']
                    row['status']='completed_interval' if h is not None else 'completed_moments_only'
                    row.update(rmse=float(np.linalg.norm(x-truth)/np.sqrt(n)),mean_width=None if h is None else float(2*h.mean()),
                        spatial_inclusion=None if h is None else float(np.mean((x-h<=truth)&(truth<=x+h))))
                    needs_diag=plan['diagnostics']=='all' or (plan['diagnostics']=='representative' and trial in plan['representatives'])
                    fixed_reference=plan['protocol']=='fixed_algorithm' or plan['comparison']=='common_k'
                    if fixed_reference or needs_diag:
                        try:
                            t=perf_counter()
                            # Variable-K diagnostics do not retain an unbounded image cache.
                            if fixed_reference:
                                if k not in mean_cache:mean_cache[k]=solve(op,clean,step,k,job,sp)
                                mean=mean_cache[k]
                            else:mean=solve(op,clean,step,k,job,sp)
                            if fixed_reference and method not in references:
                                references[method]=dict(mean_reference=mean.copy(),actual_bias=mean-truth,
                                    full_variance=covariance(sp,step['omega'],k,c['sigma'])['variance'] if sp else None,k=k)
                            if needs_diag:
                                try:
                                    diag=dict(source=source,actual_bias_norm=float(np.linalg.norm(mean-truth)),bias_allowance=ctx['bias_allowance'])
                                    if method in ('retained','corrected'):
                                        rb=bias_bound(f,dict(omega=step['omega'],step_policy='spectral'),k,c['source'],'spectral')
                                        _,bm=bias_diagnostics(op,f,step['omega'],k,truth,None if rb is None else rb['value'],None if c['eta'] is None else c['eta']['value'],job.check)
                                        diag.update(bm)
                                        if sp:
                                            full=covariance(sp,step['omega'],k,c['sigma'])['variance'];a=ctx['arrays']
                                            _,evidence=residual_evidence(full,a['retained_variance'],a['signed_residual'],a['variance'],c['epsilon']);diag['residual']=evidence
                                    diag['bias_supported']=None if ctx['bias_allowance'] is None else diag['actual_bias_norm']<=ctx['bias_allowance']+1e-10*max(1,float(np.linalg.norm(truth)))
                                    bad=source['status']=='violated' or diag['bias_supported'] is False or diag.get('retained_assumption_supported') is False or diag.get('omitted_assumption_supported') is False
                                    row['diagnostic_status']='violated' if bad else 'numerically_checked';row['diagnostics']=diag
                                except InputError as exc:
                                    row.update(diagnostic_status='unavailable',diagnostics={'reason':str(exc)})
                            diagnostic_seconds+=perf_counter()-t
                        except InputError as exc:
                            row.update(diagnostic_status='unavailable',diagnostics={'reason':str(exc)})
                    trial_results[method]=(x,h,row)
                pending.append((method,row,x,h))
            # Commit a complete trial once, after all cancelable numerical stages.
            job.check();t=perf_counter()
            for method,row,x,h in pending:
                stats[method].add(row['status'],truth,x,h,row['k']);ledger.append(row)
                dc=diagnostic_counts[method]
                if row['diagnostic_status']=='not_checked':dc['skipped']+=1
                elif row['diagnostic_status']=='unavailable':dc['unavailable']+=1
                else:dc['checked']+=1;dc['violated']+=int(row['diagnostic_status']=='violated')
                if x is not None:
                    for pixel in plan['pixels']:
                        series[method][str(pixel)].append(dict(trial_index=trial,k=row['k'],center=float(x[pixel]),
                            lower=None if h is None else float(x[pixel]-h[pixel]),upper=None if h is None else float(x[pixel]+h[pixel]),
                            inclusion=None if h is None else bool(x[pixel]-h[pixel]<=truth[pixel]<=x[pixel]+h[pixel])))
                    if trial in plan['representatives']:
                        representatives[f'{trial}_{method}']=dict(y=y.copy(),center=x.copy(),half_width=None if h is None else h.copy(),
                            lower=None if h is None else x-h,upper=None if h is None else x+h,k=row['k'],trial_index=trial,method=method,
                            variance=contexts[method]['arrays']['retained_variance'] if method=='retained' else contexts[method]['arrays']['variance'],
                            noise_half_width=(contexts[method]['arrays']['retained_sd'] if method=='retained' else contexts[method]['arrays']['pixel_sd'])*quantile(c['alpha']),
                            bias_allowance=contexts[method]['bias_allowance'])
            for i,a in enumerate(methods):
                for b in methods[i+1:]:
                    if a in trial_results and b in trial_results and trial_results[a][1] is not None and trial_results[b][1] is not None:
                        ra=trial_results[a][2];rb=trial_results[b][2]
                        pairs.setdefault(a+'__'+b,[]).append(dict(trial_index=trial,width_difference=ra['mean_width']-rb['mean_width'],spatial_inclusion_difference=ra['spatial_inclusion']-rb['spatial_inclusion']))
            attempted+=1;current_trial=None;aggregation_seconds+=perf_counter()-t
            job.progress('Committed validation trials',attempted,len(selected_indices))
        for k in plan['diagnostic_iterations']:
            job.check();t=perf_counter();mean=solve(op,clean,step,k,job,sp);b2=float(np.sum((mean-truth)**2));trace=covariance(sp,step['omega'],k,c['sigma'])['trace'] if sp else None
            bias_curve.append(dict(k=k,actual_squared_bias=b2,full_covariance_trace=trace,expected_squared_error=None if trace is None else b2+trace));diagnostic_seconds+=perf_counter()-t
    except Canceled:
        stop='partial_canceled'
        if current_trial is not None:
            for method in methods:
                stats[method].add('canceled',truth)
                ledger.append(dict(trial_index=current_trial,method=method,status='canceled',k=None,attempt=1,diagnostic_status='not_checked',reason='Canceled before atomic trial commit',measurement_entropy=seed_words(plan['root_seed'],plan['configuration_index'],current_trial,0)))
    if indices is not None and stop=='completed' and len(selected_indices)<R:stop='replayed_subset'
    summaries={};maps={}
    for method,s in stats.items():
        summary,arrays=s.result(R,plan['mc_alpha']);summary['diagnostics']=diagnostic_counts[method]
        arrays.update(references.get(method,{}));arrays.pop('k',None)
        if arrays.get('mean_reference') is not None and arrays.get('mean') is not None:arrays['mean_difference']=arrays['mean']-arrays['mean_reference']
        summaries[method]=summary;maps[method]=arrays
    return dict(status=stop,completed_trials=attempted,plan=deepcopy(plan),configuration=deepcopy(c),step=deepcopy(step),
        summaries=summaries,maps=maps,ledger=ledger,series=series,representatives=representatives,fixed_factors=fixed_payload,
        pairs=pairs,bias_curve=bias_curve,source_check=source,
        conditioning='Fixed K full-operator sampling reference' if plan['protocol']=='fixed_algorithm' or plan['comparison']=='common_k' else 'Variable-K mixture; no single Gaussian reference',
        interpretation='Empirical performance; source/bias assumptions and randomized probability conditions are not established. Retained-only intervals omit error contributions.',
        timing=dict(preparation_seconds=prep_seconds,reconstruction_seconds=reconstruction_seconds,diagnostics_seconds=diagnostic_seconds,
            aggregation_seconds=aggregation_seconds,total_seconds=perf_counter()-start),
        retention='Only predeclared representative images and pixel series retained; other trials require explicit replay; probes use recorded M4 schedule')
