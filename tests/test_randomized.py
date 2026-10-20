"""Independent algebra, stream, firewall and immutable API checks for M4."""
import itertools
import math
import unittest
from unittest.mock import patch
import numpy as np
from scipy.linalg import orth
from ct_editor.randomized.numerics import (samples,factor_record,gaussian_factors,probe_policy,direct_covariance,
    StreamingCovariance,retained_action,tsvd_split,evaluate,randomized_crossing,assemble,probability)
from ct_editor.randomized.diagnostics import bias_diagnostics,residual_evidence
from ct_editor.ct.jobs import Canceled
from ct_editor.images import InputError
from ct_editor.exact.numerics import covariance
from test_exact import MatrixOperator,prepare,ExactServiceTests
from test_ct import terminal


class RandomizedNumericalTests(unittest.TestCase):
    def test_tsvd_fixture(self):
        op,sp,_=prepare(np.diag([2.,1.,0.]));M=np.sqrt(5)/8
        arrays,meta=tsvd_split(sp,.125,2,.1,dict(M=M,nu=1),1)
        np.testing.assert_allclose(arrays['tsvd_retained_variance'],[.01*9/64,0,0])
        np.testing.assert_allclose(arrays['tsvd_omitted_variance'],[0,.01*225/4096,0])
        self.assertAlmostEqual(meta['retained_bias']['value'],M);self.assertAlmostEqual(meta['tail_bias']['value'],49*M/64)
        self.assertGreater(meta['combined_bias'],M)
        f=factor_record(np.array([4.]),np.eye(3)[:,:1]);policy=probe_policy(3,7,12,3)
        a,_,_=evaluate(op,f,.125,.1,2,policy,.5,None)
        np.testing.assert_allclose(a['variance'],arrays['tsvd_retained_variance']+2*arrays['tsvd_omitted_variance'])

    def test_gaussian_matches_same_sample_reference_and_rank_failure(self):
        rng=np.random.default_rng(19);a=rng.normal(size=(13,11));op=MatrixOperator(a)
        f=gaussian_factors(op,3,4,23,2);z=samples(11,7,23,1);q=orth((a.T@a)@z)
        values,u=np.linalg.eigh(q.T@(a.T@a)@q);v=q@u[:,-3:];expected=v@np.diag(values[-3:])@v.T
        np.testing.assert_allclose(f.vectors@np.diag(f.eigenvalues)@f.vectors.T,expected,atol=1e-11)
        g=gaussian_factors(op,3,4,23,5);np.testing.assert_allclose(retained_action(f,.001,3,.1,np.eye(11)),retained_action(g,.001,3,.1,np.eye(11)),atol=1e-15)
        self.assertEqual(f.metadata()['effective_rank'],3)
        with self.assertRaises(ValueError):f.vectors.setflags(write=True)
        with self.assertRaises(InputError):gaussian_factors(MatrixOperator(np.diag([1.]+[0.]*9)),2,4,0)
        loss=gaussian_factors(MatrixOperator(np.diag([4.,3.,2.]+[0.]*7)),2,4,0)
        self.assertTrue(loss.metadata()['sample_rank_loss'])
        with self.assertRaises(InputError):gaussian_factors(op,9,4,0)

    def test_stream_prefix_and_roles(self):
        z=samples(12,7,5,2)
        np.testing.assert_array_equal(z,np.column_stack([samples(12,2,5,2),samples(12,5,5,2,2)]))
        np.testing.assert_array_equal(z[:,:3],samples(12,3,5,2))
        self.assertTrue(np.all(np.isin(z,[-1,1])))
        self.assertFalse(np.array_equal(samples(12,7,5,1),z))
        self.assertEqual(probe_policy(12,7,5,2)['probe_id'],probe_policy(12,7,5,7)['probe_id'])

    def test_full_direct_streaming_and_spectral_actions(self):
        rng=np.random.default_rng(8)
        for a in (rng.normal(size=(6,4)),np.diag([2.,1e-7,0.]),np.array([[1.,2.,3.]])):
            op,sp,_=prepare(a);L=sp.metadata()['lambda_max'];z=rng.normal(size=(a.shape[1],3))
            for omega in (.5/L,1.8/L):
                for sigma in (0,.2):
                    rec=StreamingCovariance(op,omega,sigma,z);T=np.zeros((a.shape[1],a.shape[0]));G=np.eye(a.shape[1])-omega*a.T@a
                    for k in range(1,16):
                        T=G@T+omega*a.T;expected=sigma**2*T@T.T@z
                        np.testing.assert_allclose(direct_covariance(op,omega,k,sigma,z),expected,rtol=1e-10,atol=1e-14)
                        np.testing.assert_allclose(rec.advance(),expected,rtol=1e-10,atol=1e-14)
                        np.testing.assert_allclose(retained_action(sp,omega,k,sigma,z),expected,rtol=1e-10,atol=1e-14)
        weak=MatrixOperator(np.diag([1.,1e-12,0.]));z=np.eye(3);rec=StreamingCovariance(weak,.5,1,z)
        for _ in range(20):result=rec.advance()
        np.testing.assert_allclose(result[1,1],100e-24,rtol=1e-13);self.assertEqual(result[2,2],0)

    def test_signed_residual_noncommuting_bias(self):
        op=MatrixOperator(np.diag([2.,1.]));v=np.ones((2,1))/np.sqrt(2);f=factor_record(np.array([2.5]),v)
        D=direct_covariance(op,.125,1,1,np.eye(2))-retained_action(f,.125,1,1,np.eye(2))
        np.testing.assert_allclose(D,.125**2*np.array([[2.75,-1.25],[-1.25,-.25]]),atol=1e-16)
        self.assertLess(np.linalg.det(D),0)
        arrays,meta=bias_diagnostics(op,f,.125,1,np.array([1.,0.]),55/128,1)
        np.testing.assert_allclose(-arrays['retained_bias_vector'],[.25,7/16])
        np.testing.assert_allclose(-arrays['omitted_bias_vector'],[.25,-7/16])
        self.assertFalse(meta['retained_assumption_supported']);self.assertAlmostEqual(meta['propagated_inner_product'],-33/256)
        _,zero=bias_diagnostics(op,f,.125,1,np.zeros(2),0,0)
        self.assertEqual(zero['ratio_status'],'both_zero');self.assertIsNone(zero['omitted_retained_ratio'])

    def test_rademacher_enumeration_and_zero_diagonal(self):
        z=np.array(list(itertools.product([-1.,1.],repeat=2))).T;D=np.array([[2.,3.],[3.,-1.]])
        values=z*(D@z);np.testing.assert_array_equal(values.mean(axis=1),[2,-1]);np.testing.assert_array_equal(values.var(axis=1),[9,9])
        pair=np.array([(values[:,i]+values[:,j])/2 for i in range(4) for j in range(4)])
        np.testing.assert_array_equal(pair.var(axis=0),[4.5,4.5])
        _,evidence=residual_evidence(np.zeros(2),np.zeros(2),np.ones(2),np.ones(2),.5)
        self.assertEqual(evidence['relative_event_failures'],2);self.assertFalse(evidence['control_established'])

    def test_planner_fixture_underflow_and_intervals(self):
        op=MatrixOperator(np.eye(2));f=factor_record(np.array([1.]),np.eye(2)[:,:1]);p=probe_policy(2,3,0,2);source=dict(M=.5,nu=1)
        chosen,last=randomized_crossing(op,f,.5,.125,source,p,.5,5)
        self.assertEqual(chosen['selected_k'],2);self.assertEqual([h['crossed'] for h in chosen['history']],[False,True])
        self.assertAlmostEqual(chosen['history'][-1]['threshold'],3*np.sqrt(2)/32)
        short,_=randomized_crossing(op,f,.5,.125,source,p,.5,1);self.assertEqual(short['status'],'criterion_not_met')
        under,_=randomized_crossing(op,f,.5,0,source,p,.5,5000);self.assertIsNone(under['selected_k'])
        arrays,bound=last
        out,meta=assemble(np.array([-2.,3.]),arrays,bound,dict(value=.5),.05)
        np.testing.assert_allclose(out['total_half_width'],meta['gaussian_quantile']*np.sqrt(arrays['variance'])+1.5*.125)
        self.assertLess(out['lower'][0],-2);self.assertGreater(out['upper'][1],3)
        self.assertNotIn('lower',assemble(np.zeros(2),arrays,bound,None,.05)[0])
        def cancel():raise Canceled()
        stopped,_=randomized_crossing(op,f,.5,.125,source,p,.5,5,cancel);self.assertEqual(stopped['status'],'canceled')

    def test_nonmonotone_search_and_probability_scope(self):
        from ct_editor.randomized.numerics import corrected_quantities
        op=MatrixOperator(np.eye(2));f=factor_record(np.array([1.]),np.eye(2)[:,:1]);policy=probe_policy(2,2,0,2)
        def fluctuating(*args):
            arrays,bound=corrected_quantities(*args);k=args[2]
            arrays['pixel_sd']=np.full(2,[.1,2.,.01,3.][k-1])
            return arrays,dict(value=1,log_value=0,exact_zero=False,numerical_status='finite')
        with patch('ct_editor.randomized.numerics.corrected_quantities',side_effect=fluctuating):
            chosen,_=randomized_crossing(op,f,.5,.1,dict(M=1,nu=1),policy,.2,4)
        self.assertEqual(chosen['selected_k'],2);self.assertEqual(len(chosen['history']),2)
        c=dict(oversampling=10,alpha=.05,delta=.01,epsilon=.2,probe_count=32,source=dict(M=1,nu=.5),eta=dict(value=.1),
            selection=dict(mode='automatic',provenance='randomized_first_crossing'),settings_origin='declared_independent')
        status=probability(c,f,2)
        self.assertEqual(status['probe_control']['iteration_scope'],'selected_k_same_probes')
        self.assertFalse(status['coverage_expression_applicable']);self.assertAlmostEqual(status['reference_randomized_coverage_expression'],.95*(1-3*math.exp(-10))*.99)


class RandomizedServiceTests(unittest.TestCase):
    setUp=ExactServiceTests.setUp
    tearDown=ExactServiceTests.tearDown
    job=ExactServiceTests.job
    request=ExactServiceTests.request
    run_exact=ExactServiceTests.run_exact
    def rrequest(self,**updates):
        body=dict(request_id='random',observation_id=self.oid,rank=8,oversampling=4,sketch_seed=0,probe_seed=0,probe_count=4,block_size=2,
            epsilon=.2,delta=.01,eta=None,backend='matrix_free',reference=False,source=None,alpha=.05,
            settings_origin='unverified',declaration='Test declaration',step_policy='safe_induced_norm_v1',
            selection=dict(mode='fixed',iterations=5,provenance='fixed_predeclared'))
        body.update(updates);return body
    def run_random(self,**updates):
        job=self.job('/api/randomized-analyses',self.rrequest(**updates))
        return self.c.get('/api/randomized-analyses/'+job['result']['analysis_id']).json
    def test_matrix_free_no_hidden_svd_and_noise_seed_invariance(self):
        # Small declared source bound forces a crossing within this test's budget.
        source=dict(M=.0001,nu=.5,provenance='Test');eta=dict(value=.1,provenance='Test')
        with patch.object(self.ex,'spectrum',side_effect=AssertionError('Hidden full SVD')):
            a=self.run_random(source=source,eta=eta,selection=dict(mode='automatic',budget=30))
            self.assertEqual(a['status'],'completed');self.assertFalse(a['coverage_expression_applicable'])
            noise=self.job('/api/acquisitions/'+self.aid+'/observations',dict(request_id='noise',noise=dict(sigma=.01,seed=123)))
            b=self.run_random(source=source,eta=eta,observation_id=noise['result']['observation_id'],selection=dict(mode='automatic',budget=30))
        for key in ('selected_k','signed_residual','variance','total_half_width'):self.assertEqual(a[key],b[key])
        self.assertNotEqual(a['center'],b['center'])
        c=self.run_random(source=source,eta=dict(value=2,provenance='Test'),alpha=.01,selection=dict(mode='automatic',budget=30))
        self.assertEqual(c['selected_k'],a['selected_k']);self.assertNotEqual(c['total_half_width'],a['total_half_width'])
    def test_common_k_reference_inspection_diagnostics(self):
        exact=self.run_exact(source=dict(M=6,nu=.5,provenance='Declared'))
        request=self.rrequest();common={k:request[k] for k in ('request_id','rank','oversampling','sketch_seed','probe_seed','probe_count','block_size','epsilon','delta','eta','backend','reference')}
        common.update(exact_analysis_id=exact['analysis_id'],eta=dict(value=.1,provenance='Declared'))
        job=self.job('/api/uncertainty-comparisons',common);aid=job['result']['analysis_id'];a=self.c.get('/api/randomized-analyses/'+aid).json
        self.assertEqual(a['center'],exact['center']);self.assertEqual(a['step'],exact['step']);self.assertEqual(a['full_variance'],exact['variance'])
        np.testing.assert_allclose(np.array(a['tsvd_retained_variance'])+a['tsvd_omitted_variance'],a['full_variance'],atol=1e-16)
        before=self.c.get('/api/randomized-analyses/'+aid).json
        diag=self.job('/api/randomized-analyses/'+aid+'/diagnostics',dict(request_id='diag'))
        d=self.c.get('/api/approximation-diagnostics/'+diag['result']['diagnostic_id']).json
        self.assertEqual(before,self.c.get('/api/randomized-analyses/'+aid).json);self.assertIn('retained_assumption_supported',d)
        job=self.job('/api/randomized-analyses/'+aid+'/inspection-requests',dict(request_id='inspect',iteration=2))
        v=self.c.get('/api/randomized-views/'+job['result']['view_id']).json
        self.assertEqual(v['selected_k'],a['selected_k']);self.assertEqual(v['iteration'],2);self.assertEqual(v['probe_policy'],a['probe_policy'])
        self.assertEqual(v['selection_status'],'retrospective_inspection')
        own=self.job('/api/randomized-analyses/'+aid+'/stopping-comparisons',dict(request_id='own',budget=3,c0=1,bound_method='spectral'))
        self.assertEqual(self.c.get('/api/stopping-comparisons/'+own['result']['comparison_id']).json['selections']['exact']['status'],'criterion_not_met')
    def test_validation_missing_assumptions_and_capacity(self):
        for update in ({'sigma':1},{'truth':[0]},{'control_established':True},{'oversampling':3},{'rank':253},{'epsilon':1},{'eta':{'value':-1,'provenance':'x'}},{'probe_count':True}):
            self.assertEqual(self.c.post('/api/randomized-analyses',json=self.rrequest(**update)).status_code,400)
        a=self.run_random();self.assertIsNone(a['lower']);self.assertIsNone(a['full_variance'])
        no=self.run_random(source=dict(M=100,nu=.5,provenance='Test'),selection=dict(mode='automatic',budget=2))
        self.assertEqual(no['status'],'criterion_not_met');self.assertIsNone(no['selected_k']);self.assertIsNone(no['center'])
        self.app.extensions['randomized_service'].max_work=1
        self.assertEqual(self.c.post('/api/randomized-analyses',json=self.rrequest()).status_code,507)
        self.assertEqual(a,self.c.get('/api/randomized-analyses/'+a['analysis_id']).json)

    def test_matrix_free_64_zero_noise_and_reference_unavailable(self):
        from test_images import request_for
        sid=self.c.post('/api/snapshots',json=request_for(np.zeros((512,512),np.uint8),64)).json['snapshot_id']
        scan=self.job('/api/acquisitions',dict(request_id='64scan',snapshot_id=sid,
            geometry=dict(n_angles=8,n_detectors=92,start_degrees=0,end_degrees=180),noise=dict(sigma=0,seed=19)))['result']
        with patch.object(self.ex,'spectrum',side_effect=AssertionError('Hidden full SVD')):
            a=self.run_random(observation_id=scan['observation_id'],reference=True,source=dict(M=1,nu=.5,provenance='Test'),eta=dict(value=0,provenance='Explicit assumption'))
        self.assertEqual(a['shape'],[64,64]);self.assertEqual(a['reference']['status'],'unavailable')
        self.assertFalse(any(a['variance']));self.assertTrue(a['lower']);self.assertFalse(any(a['center']))
        self.assertEqual(a['probe_result']['normal_column_actions'],0)
        noisy=self.job('/api/acquisitions/'+scan['acquisition_id']+'/observations',dict(request_id='64noise',noise=dict(sigma=.01,seed=20)))['result']
        with patch.object(self.ex,'spectrum',side_effect=AssertionError('Hidden full SVD')):
            b=self.run_random(observation_id=noisy['observation_id'],reference=True)
        self.assertEqual(len(b['variance']),4096);self.assertGreater(max(b['variance']),0)
        self.assertIsNone(b['full_variance']);self.assertIsNone(b['lower'])

    def test_cancellation_capture_truth_and_record_limits(self):
        from threading import Event
        from ct_editor.ct.jobs import Job
        started=Event();release=Event()
        self.ct.runner.submit('test','block',{},lambda job:(started.set(),release.wait(3)))
        self.assertTrue(started.wait(1))
        request=self.rrequest(source=dict(M=1,nu=.5,provenance='Captured'),eta=dict(value=.1,provenance='Captured'))
        reply=self.app.extensions['randomized_service'].submit(request)
        request['rank']=255;request['source']['M']=500;request['eta']['value']=20;release.set()
        done=terminal(self.c,reply['job_id']);self.assertEqual(done['status'],'completed',done)
        a=self.c.get('/api/randomized-analyses/'+done['result']['analysis_id']).json
        self.assertEqual(a['configuration']['source']['M'],1);self.assertEqual(a['configuration']['eta']['value'],.1)
        truth=self.ct.snapshots.array(self.sid)
        with patch.object(self.ct.snapshots,'array',return_value=np.zeros_like(truth)):
            diag=self.job('/api/randomized-analyses/'+a['analysis_id']+'/diagnostics',dict(request_id='truth-only'))
        d=self.c.get('/api/approximation-diagnostics/'+diag['result']['diagnostic_id']).json
        self.assertEqual(d['actual_bias_norm'],0);self.assertEqual(a,self.c.get('/api/randomized-analyses/'+a['analysis_id']).json)
        original=Job.progress;factor_count=len(self.ct.records['low_rank_factors'])
        def stop(job,stage,completed,total):
            original(job,stage,completed,total)
            if stage=='Searching randomized crossing' and completed==3:job.event.set()
        with patch.object(Job,'progress',stop):
            r=self.c.post('/api/randomized-analyses',json=self.rrequest(source=dict(M=100,nu=.5,provenance='Test'),selection=dict(mode='automatic',budget=20)))
            canceled=terminal(self.c,r.json['job_id'])
        self.assertEqual(canceled['status'],'canceled')
        partial=self.c.get('/api/randomized-analyses/'+canceled['result']['analysis_id']).json
        self.assertIsNone(partial['selected_k']);self.assertIsNone(partial['lower'])
        self.assertEqual(len(self.c.get('/api/randomized-analyses/'+partial['analysis_id']+'/stopping-history').json['history']),3)
        self.assertEqual(len(self.ct.records['low_rank_factors']),factor_count)
        def stop_probe(job,stage,completed,total):
            original(job,stage,completed,total)
            if stage=='Probing covariance residual' and completed==2:job.event.set()
        count=len(self.ct.records['randomized_analyses'])
        with patch.object(Job,'progress',stop_probe):
            r=self.c.post('/api/randomized-analyses',json=self.rrequest())
            canceled=terminal(self.c,r.json['job_id'])
        self.assertEqual(canceled['status'],'canceled');self.assertEqual(len(self.ct.records['randomized_analyses']),count)
        self.assertEqual(len(self.ct.records['low_rank_factors']),factor_count)
        self.ct.limits['randomized_analyses']=count
        self.assertEqual(self.c.post('/api/randomized-analyses',json=self.rrequest()).status_code,507)
        self.assertEqual(a,self.c.get('/api/randomized-analyses/'+a['analysis_id']).json)

# Imported helpers are not a second copy of the M3 test suite.
del ExactServiceTests
