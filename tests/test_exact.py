import inspect
import math
import time
import unittest
from threading import Event
from unittest.mock import patch
import numpy as np
from scipy.sparse import csr_matrix
from scipy.stats import norm

from ct_editor import create_app
from ct_editor.images import InputError
from ct_editor.ct.service import digest
from ct_editor.ct.solver import landweber, frozen_array
from ct_editor.ct.jobs import Canceled
from ct_editor.exact.spectrum import decompose, FullSpectrum
from ct_editor.exact.numerics import (filters,covariance,covariance_action,bias_bound,intervals,first_crossing,
    source_config,resolve_exact_step,joint_status,quantile)
from ct_editor.exact.diagnostics import source_check,diagnose
from test_images import request_for
from test_ct import terminal


class MatrixOperator:
    def __init__(self,a):
        self.a=np.array(a,dtype=float);self.shape=self.a.shape
    def sparse_copy(self): return csr_matrix(self.a.copy())
    def metadata(self):
        key=digest({'shape':self.shape},self.a)
        return dict(operator_id=key,csr_checksum=key,L_safe=float(np.linalg.norm(self.a,1)*np.linalg.norm(self.a,np.inf)))
    def forward(self,x): return self.a@x
    def adjoint(self,y): return self.a.T@y


def prepare(a,policy='exact_spectral_half'):
    op=MatrixOperator(a);sp=decompose(op);step=resolve_exact_step(op,sp,policy)
    return op,sp,step


class ExactNumericalTests(unittest.TestCase):
    def test_hand_covariance_bias_and_source_fixture(self):
        op,sp,step=prepare(np.diag([2,1,0]));M=np.sqrt(5)/8;source=dict(nu=1.,M=M,provenance='algebra fixture')
        c=covariance(sp,.125,2,.1)
        np.testing.assert_allclose(c['variance'],.01*np.array([9/64,225/4096,0]),rtol=1e-14)
        self.assertAlmostEqual(c['trace'],sum(c['variance']))
        spec=bias_bound(sp,step,2,source,'spectral');glob=bias_bound(sp,step,2,source,'global')
        self.assertAlmostEqual(spec['value'],M);self.assertAlmostEqual(glob['value'],32*M/27)
        truth=np.array([.5,.25,0]);result=landweber(op,op.forward(truth),.125,2)['final']
        np.testing.assert_allclose(result-truth,[-1/8,-49/256,0])
        self.assertEqual(source_check(sp,truth,source)['status'],'numerically_checked')
        self.assertEqual(source_check(sp,truth+[0,0,1],source)['status'],'violated')
        self.assertEqual(source_check(sp,truth,{**source,'M':.001})['status'],'violated')
        self.assertEqual(joint_status(sp,c,.1,2)['reason'],'rank_deficient_or_unresolved')
        arrays,z=intervals(result,c,spec,.05)
        np.testing.assert_allclose(arrays['total_half_width'],norm.ppf(.975)*np.sqrt(c['variance'])+M)
        np.testing.assert_allclose(arrays['lower'],result-arrays['total_half_width'])
        self.assertEqual(arrays['total_half_width'][2],spec['value'])
        self.assertTrue((arrays['lower']<0).any())
        self.assertGreater(np.linalg.norm(result-truth),0)

    def test_direct_T_covariance_action_reconstruction_and_rectangular(self):
        for a in (np.array([[2,1],[1,3],[1,0.5]]),np.array([[1,2,3],[0,1,1]])):
            op,sp,step=prepare(a);omega=step['omega'];G=np.eye(a.shape[1])-omega*a.T@a
            T=np.zeros((a.shape[1],a.shape[0]));y=np.arange(a.shape[0],dtype=float)-2
            for k in range(0,8):
                c=covariance(sp,omega,k,.3);gamma=.09*T@T.T
                np.testing.assert_allclose(c['variance'],np.diag(gamma),rtol=1e-12,atol=1e-15)
                v=np.arange(a.shape[1])-1.5
                np.testing.assert_allclose(covariance_action(sp,omega,k,.3,v),gamma@v,rtol=1e-12,atol=1e-15)
                if k: np.testing.assert_allclose(landweber(op,y,omega,k,step_certificate=sp.certificate())['final'],T@y,atol=1e-12)
                T=G@T+omega*a.T
            self.assertEqual(sp.metadata()['modes_computed'],min(a.shape))
            self.assertLess(sp.metadata()['decomposition_residual'],1e-12)

    def test_tiny_zero_negative_filters_and_rank_is_diagnostic(self):
        lam=np.array([0,1e-250,1e-18,1.,3.])
        for k in (0,1,2,101):
            c,a=filters(lam,.5,k);reference=np.zeros(len(lam))
            for _ in range(k): reference=(1-.5*lam)*reference+.5
            np.testing.assert_allclose(c,reference,rtol=1e-13)
            np.testing.assert_allclose(a,lam*reference**2,rtol=1e-13)
        _,sp,step=prepare(np.diag([1.,1e-9]))
        alt=decompose(MatrixOperator(np.diag([1.,1e-9])),rank_multiplier=1e9)
        self.assertNotEqual(sp.metadata()['numerical_rank'],alt.metadata()['numerical_rank'])
        np.testing.assert_array_equal(covariance(sp,.5,20,.1)['variance'],covariance(alt,.5,20,.1)['variance'])
        self.assertGreater(covariance(alt,.5,20,.1)['variance'][1],0)
        np.testing.assert_allclose(filters(np.array([1e-250]),.5,100)[1],[2500e-250],rtol=1e-14)
        with self.assertRaises(ValueError): sp.vectors[0,0]=99

    def test_scaling_zero_and_rotation_invariance(self):
        _,sp,step=prepare(np.eye(3))
        angle=.31;v=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,-1]])
        rotated=FullSpectrum(sp.header,sp.singular_values,sp.eigenvalues,frozen_array(v),frozen_array(v*v))
        c=covariance(sp,.5,10,.1);d=covariance(rotated,.5,10,.1)
        np.testing.assert_allclose(c['variance'],d['variance'],rtol=1e-14)
        np.testing.assert_allclose(covariance(sp,.5,10,.2)['variance'],4*c['variance'])
        self.assertFalse(covariance(sp,.5,0,.1)['variance'].any())
        self.assertFalse(covariance(sp,.5,10,0)['variance'].any())
        self.assertEqual(joint_status(sp,c,0,10)['reason'],'zero_noise')
        self.assertEqual(joint_status(sp,c,.1,10)['reason'],'optional_joint_calculation_not_implemented')
        previous=np.zeros(3)
        for k in range(20):
            current=covariance(sp,.5,k,.1)['variance'];self.assertTrue(np.all(current>=previous));previous=current

    def test_stopping_fixture_equality_no_crossing_and_cancellation(self):
        _,sp,step=prepare([[1.]])
        source=dict(nu=1.,M=.5)
        result=first_crossing(sp,step,1/8,source,'spectral',1,20)
        self.assertEqual(result['selected_k'],3);self.assertEqual(len(result['history']),3)
        np.testing.assert_allclose([h['bias_bound'] for h in result['history']],[.25,.125,.0625])
        self.assertEqual(first_crossing(sp,step,1/6,source,'spectral',1,20)['selected_k'],2)
        self.assertIsNone(first_crossing(sp,step,1/8,source,'spectral',1,2)['selected_k'])
        result=first_crossing(sp,step,0,source,'spectral',1,5000)
        self.assertEqual(result['status'],'criterion_not_met');self.assertEqual(result['history'][-1]['numerical_status'],'underflow')
        self.assertEqual(first_crossing(sp,step,0,dict(nu=1,M=0),'spectral',1,10)['selected_k'],1)
        count=0
        def check():
            nonlocal count
            count+=1
            if count==3: raise Canceled()
        canceled=first_crossing(sp,step,0,source,'spectral',1,20,check)
        self.assertEqual(canceled['status'],'canceled');self.assertEqual(len(canceled['history']),2)
        self.assertNotIn('y',inspect.signature(first_crossing).parameters)
        self.assertNotIn('alpha',inspect.signature(first_crossing).parameters)
        self.assertNotIn('truth',inspect.signature(covariance).parameters)

    def test_global_compatibility_oscillatory_and_spectral_certificate(self):
        op,sp,step=prepare([[1,1],[0,1]])
        inherited=resolve_exact_step(op,sp,None,dict(omega=.6,step_policy='manual_safe_bound_v1'))
        self.assertFalse(inherited['nonoscillatory_certified'])
        c=covariance(sp,.6,3,.1)
        G=np.eye(2)-.6*op.a.T@op.a;T=.6*(np.eye(2)+G+G@G)@op.a.T
        np.testing.assert_allclose(c['variance'],np.diag(.01*T@T.T))
        source=dict(nu=.5,M=2.)
        with self.assertRaises(InputError): first_crossing(sp,inherited,.1,source,'spectral',1,10)
        with self.assertRaises(InputError): bias_bound(sp,inherited,2,source,'global')
        safe=resolve_exact_step(op,sp,'safe_induced_norm_v1')
        self.assertGreaterEqual(bias_bound(sp,safe,20,source,'safe_global')['value'],bias_bound(sp,safe,20,source,'spectral')['value'])
        with self.assertRaises(InputError): landweber(MatrixOperator([[2.]]),np.ones(1),.1,2,step_certificate=sp.certificate())
        # A spectral step outside M2's conservative manual range is explicitly supported.
        wide=MatrixOperator(np.array([[1.,1.,1.,1.],[1.,0.,0.,0.],[1.,0.,0.,0.],[1.,0.,0.,0.]]))
        wsp=decompose(wide);omega=1.5/wsp.metadata()['lambda_max']
        with self.assertRaises(InputError): landweber(wide,np.ones(4),omega,3)
        self.assertEqual(landweber(wide,np.ones(4),omega,3,step_certificate=wsp.certificate())['completed_iterations'],3)

    def test_absent_source_quantiles_diagnostics_and_underflow(self):
        _,sp,step=prepare([[1.]])
        c=covariance(sp,.5,2,.1)
        arrays,_=intervals(np.array([-2.]),c,None,.05)
        self.assertNotIn('lower',arrays)
        self.assertGreater(intervals(np.zeros(1),c,dict(value=1,numerical_status='finite'),.01)[0]['total_half_width'][0],
                           intervals(np.zeros(1),c,dict(value=1,numerical_status='finite'),.05)[0]['total_half_width'][0])
        for alpha in (0,1,float('nan')):
            with self.assertRaises(InputError): quantile(alpha)
        for source in ({'nu':0,'M':1,'provenance':'x'},{'nu':1,'M':-1,'provenance':'x'},{'nu':1,'M':1,'verified':True}):
            with self.assertRaises(InputError): source_config(source)
        b=bias_bound(sp,step,5000,dict(nu=1,M=1),'spectral')
        self.assertFalse(b['exact_zero']);self.assertEqual(b['numerical_status'],'underflow')
        with self.assertRaises(InputError): intervals(np.zeros(1),c,b,.05)
        arr,meta=diagnose(np.array([.2]),np.array([.1]),np.array([0.]),.01,np.array([-.3]),np.array([.3]))
        np.testing.assert_allclose(arr['error'],arr['actual_bias']+arr['observed_random'])
        self.assertEqual(meta['inclusion_fraction'],1);self.assertIsNone(meta['relative_error']);self.assertFalse(meta['bias_bound_supported'])


class ExactServiceTests(unittest.TestCase):
    def setUp(self):
        self.app=create_app({'TESTING':True});self.c=self.app.test_client();self.ct=self.app.extensions['ct_service'];self.ex=self.app.extensions['exact_service']
        image=np.zeros((512,512),np.uint8);image[55:270,83:245]=192;image[310:380,290:425]=83
        self.sid=self.c.post('/api/snapshots',json=request_for(image,16)).json['snapshot_id']
        refs=self.job('/api/acquisitions',dict(request_id='scan',snapshot_id=self.sid,
            geometry=dict(n_angles=12,n_detectors=24,start_degrees=0,end_degrees=180),noise=dict(sigma=.01,seed=19)))['result']
        self.oid=refs['observation_id'];self.aid=refs['acquisition_id']
    def tearDown(self): self.ct.runner.close()
    def job(self,url,data):
        response=self.c.post(url,json=data);self.assertEqual(response.status_code,202,response.json)
        result=terminal(self.c,response.json['job_id']);self.assertEqual(result['status'],'completed',result)
        return result
    def request(self,**updates):
        body=dict(request_id='exact',observation_id=self.oid,source=None,bound_method='spectral',alpha=.05,
            settings_origin='unverified',declaration='No independence claim.',step_policy='exact_spectral_half',
            selection=dict(mode='fixed',iterations=20,provenance='imported_selection_unknown'))
        body.update(updates);return body
    def run_exact(self,**updates):
        job=self.job('/api/exact-analyses',self.request(**updates))
        return self.c.get('/api/exact-analyses/'+job['result']['analysis_id']).json
    def test_attach_new_inspection_and_no_source(self):
        run=self.job('/api/reconstructions',dict(request_id='m2',observation_id=self.oid,iterations=201,step={'mode':'automatic_safe'}))['result']['reconstruction_id']
        original=self.c.get('/api/reconstructions/'+run).json
        body={k:v for k,v in self.request().items() if k not in ('observation_id','selection','step_policy')}
        body.update(iteration=2,selection_provenance='retrospective_inspection')
        job=self.job('/api/reconstructions/'+run+'/exact-analyses',body)
        record=self.c.get('/api/exact-analyses/'+job['result']['analysis_id']).json
        self.assertEqual(record['step']['omega'],original['configuration']['omega'])
        self.assertEqual(record['iteration'],2);self.assertIsNone(record['lower']);self.assertEqual(record['source_condition_status'],'not_supplied')
        self.assertEqual(original,self.c.get('/api/reconstructions/'+run).json)
        viewjob=self.job('/api/exact-analyses/'+record['analysis_id']+'/inspection-requests',dict(request_id='inspect',iteration=5))
        view=self.c.get('/api/exact-views/'+viewjob['result']['view_id']).json
        self.assertEqual(view['selected_k'],2);self.assertEqual(view['iteration'],5)
        self.assertNotEqual(view['variance'],record['variance']);self.assertNotEqual(view['center'],record['center'])
        self.assertEqual(view['selection_status'],'retrospective_inspection')
        viewjob=self.job('/api/exact-analyses/'+record['analysis_id']+'/inspection-requests',dict(request_id='zero',iteration=0))
        zero=self.c.get('/api/exact-views/'+viewjob['result']['view_id']).json
        self.assertFalse(any(zero['variance']));self.assertIsNone(zero['lower'])
        self.assertEqual(zero['intervals_unavailable_reason'],'initialization')

    def test_width_seed_invariance_and_diagnostics_do_not_change_inference(self):
        source=dict(M=1,nu=.5,provenance='Explicit unverified test declaration.')
        a=self.run_exact(source=source);before=self.c.get('/api/exact-analyses/'+a['analysis_id']).json
        with patch('ct_editor.ct.service.sample_observation',side_effect=AssertionError('Noise resampled')):
            diagjob=self.job('/api/exact-analyses/'+a['analysis_id']+'/diagnostics',dict(request_id='diagnose'))
        diagnostic=self.c.get('/api/exact-diagnostics/'+diagjob['result']['diagnostic_id']).json
        self.assertEqual(before,self.c.get('/api/exact-analyses/'+a['analysis_id']).json)
        self.assertEqual(diagnostic['analysis_id'],a['analysis_id']);self.assertLess(diagnostic['decomposition_residual'],1e-12)
        noise=self.job('/api/acquisitions/'+self.aid+'/observations',dict(request_id='noise',noise=dict(sigma=.01,seed=20)))
        b=self.run_exact(source=source,observation_id=noise['result']['observation_id'])
        for key in ('variance','pixel_sd','noise_half_width','total_half_width'): self.assertEqual(a[key],b[key])
        self.assertNotEqual(a['center'],b['center']);self.assertTrue(b['spectrum_cache_hit'])
        for key in ('center','variance','lower'): self.assertNotIn(key,self.c.get('/api/exact-analyses').json['exact_analyses'][0])

    def test_automatic_no_crossing_cap_and_binding(self):
        no=self.run_exact(source=dict(M=100,nu=.5,provenance='Unverified'),selection=dict(mode='automatic',budget=2,c0=1))
        self.assertEqual(no['status'],'criterion_not_met');self.assertIsNone(no['selected_k']);self.assertIsNone(no['center'])
        history=self.c.get('/api/exact-analyses/'+no['analysis_id']+'/stopping-history').json['history'];self.assertEqual(len(history),2)
        cap=self.job('/api/exact-analyses/'+no['analysis_id']+'/cap-reconstruction',dict(request_id='cap'))
        capr=self.c.get('/api/exact-analyses/'+cap['result']['analysis_id']).json
        self.assertEqual(capr['selection_status'],'budget_cap_fallback');self.assertEqual(capr['iteration'],2)
        yes=self.run_exact(source=dict(M=0,nu=.5,provenance='Zero source assertion'),selection=dict(mode='automatic',budget=20,c0=1),settings_origin='declared_independent')
        self.assertEqual(yes['selected_k'],1);r=self.c.get('/api/reconstructions/'+yes['reconstruction_id']).json
        self.assertEqual(r['completed_iterations'],1);self.assertEqual(r['configuration']['omega'],yes['step']['omega'])
        np.testing.assert_array_equal(r['pixels'],yes['center'])
        yes2=self.run_exact(source=dict(M=0,nu=.5,provenance='Zero source assertion'),selection=dict(mode='automatic',budget=20,c0=1),alpha=.01)
        self.assertEqual(yes2['selected_k'],yes['selected_k'])

    def test_invalid_resource_size_and_ownership(self):
        for updates in ({'sigma':1},{'truth':[0]},{'rank':10},{'source':{'nu':1}},
                        {'selection':dict(mode='fixed',iterations=True,provenance='fixed_predeclared')},
                        {'bound_method':'safe_global'},{'alpha':1}):
            self.assertIn(self.c.post('/api/exact-analyses',json=self.request(**updates)).status_code,(400,422))
        self.ex.max_elements=1
        with patch('ct_editor.exact.service.decompose',side_effect=AssertionError('Dense allocation before preflight')):
            self.assertEqual(self.c.post('/api/exact-analyses',json=self.request()).status_code,507)
        self.ex.max_elements=8_000_000
        big=self.c.post('/api/snapshots',json=request_for(np.zeros((512,512),np.uint8),64)).json
        obs=self.job('/api/acquisitions',dict(request_id='64',snapshot_id=big['snapshot_id'],
            geometry=dict(n_angles=2,n_detectors=4,start_degrees=0,end_degrees=180),noise=dict(sigma=0,seed=0)))
        response=self.c.post('/api/exact-analyses',json=self.request(observation_id=obs['result']['observation_id']))
        self.assertEqual(response.status_code,422);self.assertEqual(response.json['error']['code'],'unsupported_exact_size')
        result=self.run_exact();sp=next(iter(self.ex.cache.values()))[0]
        with self.assertRaises(ValueError): sp.eigenvalues[0]=0
        result['variance'][0]=99
        self.assertNotEqual(self.c.get('/api/exact-analyses/'+result['analysis_id']).json['variance'][0],99)

    def test_cancellation_during_decomposition_and_search(self):
        started=Event();release=Event();baseline=self.ct.budget.used
        def blocked(op,check,progress):
            started.set();release.wait(3);check();raise AssertionError('Canceled SVD continued')
        with patch('ct_editor.exact.service.decompose',side_effect=blocked):
            response=self.c.post('/api/exact-analyses',json=self.request())
            self.assertTrue(started.wait(1));self.assertEqual(self.c.get('/api/health').status_code,200)
            self.c.post('/api/jobs/'+response.json['job_id']+'/cancel',json={});release.set()
            self.assertEqual(terminal(self.c,response.json['job_id'])['status'],'canceled')
        self.assertFalse(self.ex.cache);self.assertFalse(self.ct.list('exact_analyses'))
        # Wait for the worker's finally cleanup after terminal publication.
        for _ in range(100):
            if self.ct.budget.used==baseline: break
            time.sleep(.001)
        self.assertEqual(self.ct.budget.used,baseline)

    def test_partial_search_and_reconstruction_have_no_selected_interval(self):
        from ct_editor.ct.jobs import Job
        original=Job.progress
        def stop_search(job,stage,completed,total):
            original(job,stage,completed,total)
            if stage=='Searching bias–noise crossing' and completed==3: job.event.set()
        with patch.object(Job,'progress',stop_search):
            response=self.c.post('/api/exact-analyses',json=self.request(source=dict(M=100,nu=.5,provenance='Unverified'),
                selection=dict(mode='automatic',budget=20,c0=1)))
            job=terminal(self.c,response.json['job_id'])
        self.assertEqual(job['status'],'canceled')
        record=self.c.get('/api/exact-analyses/'+job['result']['analysis_id']).json
        self.assertEqual(record['status'],'canceled');self.assertIsNone(record['selected_k']);self.assertIsNone(record['center'])
        self.assertEqual(len(self.c.get('/api/exact-analyses/'+record['analysis_id']+'/stopping-history').json['history']),3)
        def stop_reconstruction(job,stage,completed,total):
            original(job,stage,completed,total)
            if stage=='Reconstructing selected iterate' and completed==5: job.event.set()
        with patch.object(Job,'progress',stop_reconstruction):
            response=self.c.post('/api/exact-analyses',json=self.request())
            job=terminal(self.c,response.json['job_id'])
        self.assertEqual(job['status'],'canceled')
        record=self.c.get('/api/exact-analyses/'+job['result']['analysis_id']).json
        self.assertEqual(record['selected_k'],20);self.assertIsNone(record['lower']);self.assertIsNone(record['center'])
        run=self.c.get('/api/reconstructions/'+record['reconstruction_id']).json
        self.assertEqual(run['completed_iterations'],5);self.assertEqual(run['status'],'canceled')

    def test_captured_settings_truth_firewall_and_cache_rebuild(self):
        started=Event();release=Event()
        self.ct.runner.submit('test','block',{},lambda job:(started.set(),release.wait(3)))
        self.assertTrue(started.wait(1))
        request=self.request(source=dict(M=1,nu=.5,provenance='Captured declaration'))
        reply=self.c.post('/api/exact-analyses',json=request)
        request['source']['M']=100;request['selection']['iterations']=2;release.set()
        job=terminal(self.c,reply.json['job_id']);self.assertEqual(job['status'],'completed')
        record=self.c.get('/api/exact-analyses/'+job['result']['analysis_id']).json
        self.assertEqual(record['configuration']['source']['M'],1);self.assertEqual(record['selected_k'],20)
        before=self.c.get('/api/exact-analyses/'+record['analysis_id']).json
        original_truth=self.ct.snapshots.array(self.sid)
        with patch.object(self.ct.snapshots,'array',return_value=np.zeros_like(original_truth)):
            diag=self.job('/api/exact-analyses/'+record['analysis_id']+'/diagnostics',dict(request_id='altered-truth'))
        evidence=self.c.get('/api/exact-diagnostics/'+diag['result']['diagnostic_id']).json
        self.assertEqual(evidence['source_check']['minimum_source_norm'],0)
        self.assertEqual(before,self.c.get('/api/exact-analyses/'+record['analysis_id']).json)
        with self.ct.lock:
            for _,lease in self.ex.cache.values(): lease.close()
            self.ex.cache.clear();self.ex.cache_bytes=0
        rebuilt=self.run_exact(source=dict(M=1,nu=.5,provenance='Captured declaration'))
        np.testing.assert_allclose(rebuilt['variance'],before['variance'],rtol=1e-12)
        np.testing.assert_allclose(rebuilt['total_half_width'],before['total_half_width'],rtol=1e-12)
        self.assertEqual(rebuilt['spectrum']['spectrum_id'],before['spectrum']['spectrum_id'])
