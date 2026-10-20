import inspect
import math
import time
import unittest
from threading import Event
from unittest.mock import patch

import numpy as np
from scipy.sparse import csr_matrix
from ct_editor import create_app
from ct_editor.images import InputError
from ct_editor.ct.geometry import Geometry, Operator, build_operator, native_detector_offsets
from ct_editor.ct.solver import landweber, replay, resolve_step
from ct_editor.ct.diagnostics import ImageDiagnostics
from ct_editor.ct.service import noise_settings, sample_observation
from ct_editor.ct.jobs import JobRunner, Canceled
from test_images import request_for

MATRIX=np.array([[1,0,1,0],[0,1,0,1],[0,0,1,1],[1,1,0,0]],float)
TRUTH=np.array([0,1,.5,.25])

def fixture(): return Operator(Geometry.arrays(2,[0,np.pi/2],[-.5,.5]),csr_matrix(MATRIX))

def terminal(client,job_id):
    for _ in range(3000):
        job=client.get('/api/jobs/'+job_id).json
        if job['status'] in ('completed','canceled','failed'): return job
        time.sleep(.003)
    raise AssertionError('Job failed to terminate')


class GeometryTests(unittest.TestCase):
    def test_hand_matrix_and_norms(self):
        op=fixture();np.testing.assert_allclose(op.sparse_copy().toarray(),MATRIX,atol=1e-12)
        np.testing.assert_allclose(op.forward(TRUTH),[.5,1.25,.75,1])
        self.assertEqual(op.metadata()['L_safe'],4)
        self.assertEqual(resolve_step(op,{'mode':'automatic_safe'})['omega'],.125)
        self.assertLessEqual(np.linalg.norm(MATRIX,2)**2,4+1e-12)
        copy=op.sparse_copy();copy.data[:]=0;np.testing.assert_array_equal(op.sparse_copy().toarray(),MATRIX)

    def test_subpixel_weights_and_pixel_units(self):
        # A single unit pixel at angle zero deposits 1/8, 3/4, 1/8.
        op=build_operator(Geometry.arrays(1,[0],[-1,0,1]))
        np.testing.assert_allclose(op.forward([1]),[.125,.75,.125])
        # Center of even image is MATLAB's floor((size+1)/2), not its
        # geometric center. Horizontal/vertical offsets expose orientation.
        native=native_detector_offsets(2)
        op=build_operator(Geometry.arrays(2,[0,np.pi/2],native))
        pixel=np.array([0.,1.,0.,0.])  # top right: x=1, y=0
        expected=np.zeros((2,len(native)))
        for angle,center in enumerate((1,0)):
            for shift,weight in ((-1,.125),(0,.75),(1,.125)):
                expected[angle,np.flatnonzero(native==center+shift)[0]]=weight
        np.testing.assert_allclose(op.forward(pixel).reshape(expected.shape),expected,atol=1e-14)
        # Projection mass per angle is image intensity sum in pixel units.
        for side in (3,4,32):
            offsets=native_detector_offsets(side)
            geometry=Geometry.arrays(side,[0,.31,np.pi/2],offsets)
            op=build_operator(geometry)
            y=op.forward(np.ones(side**2)).reshape(3,-1)
            np.testing.assert_allclose(y.sum(axis=1),side**2,rtol=1e-13)
        self.assertEqual(len(native_detector_offsets(32)),49)
        self.assertEqual(len(native_detector_offsets(64)),95)
        self.assertEqual(len(native_detector_offsets(128)),185)

    def test_custom_detectors_resample_native_profile(self):
        side=5;native=native_detector_offsets(side);angles=[0,.73]
        op=build_operator(Geometry.arrays(side,angles,native))
        offsets=np.array([-100,-2.25,0,.5,100])
        custom=build_operator(Geometry.arrays(side,angles,offsets))
        image=np.arange(side**2,dtype=float)
        reference=op.forward(image).reshape(2,-1)
        expected=np.array([np.interp(offsets,native,row,left=0,right=0) for row in reference])
        np.testing.assert_allclose(custom.forward(image).reshape(2,-1),expected,atol=1e-12)
        for invalid in ([1,0],[0,0]):
            with self.assertRaises(InputError): Geometry.arrays(side,angles,invalid)

    def test_arrays_linearity_adjoint_and_structure(self):
        for n,na,nd in [(3,7,8),(5,11,9),(16,17,20)]:
            g=Geometry.request(n,dict(n_angles=na,n_detectors=nd,start_degrees=10,end_degrees=140))
            self.assertAlmostEqual(g.angles[-1],np.deg2rad(10+(na-1)*130/na))
            self.assertEqual(g.offsets[0],native_detector_offsets(n)[0]);self.assertEqual(g.offsets[-1],native_detector_offsets(n)[-1])
            op=build_operator(g);a=op.sparse_copy();rng=np.random.default_rng(21)
            x=rng.normal(size=n*n);z=rng.normal(size=n*n);v=rng.normal(size=na*nd)
            np.testing.assert_allclose(op.forward(2*x-3*z),2*op.forward(x)-3*op.forward(z),atol=1e-10)
            self.assertAlmostEqual(op.forward(x)@v,x@op.adjoint(v),delta=1e-10)
            self.assertTrue(a.has_canonical_format);self.assertTrue(np.all(a.data>=0));self.assertTrue(np.all(np.isfinite(a.data)))
            self.assertLessEqual(op.forward(np.ones(n*n)).max(),n*n+1e-10)
            np.testing.assert_array_equal(op.forward(np.zeros(n*n)),np.zeros(na*nd))
            self.assertEqual(build_operator(g).metadata()['csr_checksum'],op.metadata()['csr_checksum'])
        self.assertNotEqual(Geometry.arrays(2,[0],[0]).geometry_hash,Geometry.arrays(2,[.1],[0]).geometry_hash)


class SolverTests(unittest.TestCase):
    def test_dense_reference_nullspace_and_metrics(self):
        op=fixture();y=op.forward(TRUTH)
        for k in (1,2,11):
            result=landweber(op,y,.125,k)
            G=np.eye(4)-.125*MATRIX.T@MATRIX
            T=sum((np.linalg.matrix_power(G,j) for j in range(k)),np.zeros((4,4)))@(.125*MATRIX.T)
            np.testing.assert_allclose(result['final'],T@y,atol=1e-12)
            self.assertEqual(len(result['history']),k+1)
            objectives=[h['objective'] for h in result['history']];self.assertTrue(np.all(np.diff(objectives)<=1e-12))
            residual=y-MATRIX@result['final'];self.assertAlmostEqual(result['history'][-1]['objective'],.5*(residual@residual))
        np.testing.assert_allclose(landweber(op,y,.125,1)['final'],[3/16,9/32,5/32,1/4])
        result=landweber(op,y,.125,500)
        np.testing.assert_allclose(result['final'],[5/16,11/16,3/16,9/16],atol=1e-10)
        self.assertGreater(np.linalg.norm(result['final']-TRUTH),.1)
        self.assertLessEqual(len(result['checkpoints']),101)
        actual=replay(op,y,.125,result['checkpoints'][10],10,13)
        np.testing.assert_allclose(actual,landweber(op,y,.125,13)['final'],atol=1e-12)
        with self.assertRaises(ValueError): result['checkpoints'][0][0]=1

    def test_linearity_signed_output_and_truth_firewall(self):
        op=fixture();rng=np.random.default_rng(8);y=rng.normal(size=4)*20;z=rng.normal(size=4)
        a=landweber(op,y,.125,20)['final'];b=landweber(op,z,.125,20)['final']
        np.testing.assert_allclose(landweber(op,2*y-3*z,.125,20)['final'],2*a-3*b,atol=1e-10)
        self.assertTrue((a<0).any() or (a>1).any())
        d1=ImageDiagnostics(TRUTH);d2=ImageDiagnostics(TRUTH+1)
        r1=landweber(op,y,.125,20,emit=d1.record);r2=landweber(op,y,.125,20,emit=d2.record)
        np.testing.assert_array_equal(r1['final'],r2['final'])
        self.assertEqual([h['residual_norm'] for h in r1['history']],[h['residual_norm'] for h in r2['history']])
        self.assertNotEqual(d1.history,d2.history)
        self.assertNotIn('truth',inspect.signature(landweber).parameters)
        def malicious(k,x):
            with self.assertRaises(ValueError): x[0]=999
        np.testing.assert_array_equal(landweber(op,y,.125,20,emit=malicious)['final'],a)

    def test_steps_zero_and_cancellation(self):
        op=fixture()
        for value in (0,-1,.5,float('inf'),float('nan'),True):
            with self.assertRaises(InputError): resolve_step(op,{'mode':'manual','value':value})
        self.assertFalse(resolve_step(op,{'mode':'manual','value':.4})['nonoscillatory_certified'])
        event=Event()
        def emit(k,x):
            if k==7: event.set()
        result=landweber(op,np.zeros(4),.125,100,cancel=event.is_set,emit=emit)
        self.assertEqual(result['completed_iterations'],7);self.assertEqual(result['status'],'canceled');self.assertEqual(len(result['history']),8)
        self.assertIn(7,result['checkpoints']);self.assertIsNone(result['history'][0]['relative_residual'])
        d=ImageDiagnostics(np.zeros(4));d.record(0,np.zeros(4));self.assertIsNone(d.history[0]['relative_error'])
        degenerate=Operator(Geometry.arrays(2,[0],[2]),csr_matrix((1,4)))
        with self.assertRaises(InputError): resolve_step(degenerate,{'mode':'automatic_safe'})

    def test_seed_and_zero_noise(self):
        clean=np.zeros(1000);s=noise_settings({'sigma':.01,'seed':0})
        a,noise=sample_observation(clean,s);b,_=sample_observation(clean,s)
        np.testing.assert_array_equal(a,b);self.assertTrue((a<0).any());self.assertNotEqual(noise.mean(),0)
        c,_=sample_observation(clean,noise_settings({'sigma':.01,'seed':1}));self.assertFalse(np.array_equal(a,c))
        z,e=sample_observation(clean,noise_settings({'sigma':0,'seed':0}));np.testing.assert_array_equal(z,clean);self.assertFalse(e.any())


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.app=create_app({'TESTING':True});self.client=self.app.test_client();self.service=self.app.extensions['ct_service']
        source=np.zeros((512,512),np.uint8);source[80:300,100:380]=128;source[99,99]=129
        self.snapshot=self.client.post('/api/snapshots',json=request_for(source,16)).json
    def tearDown(self): self.service.runner.close()
    def acquire(self,**updates):
        body=dict(request_id='acq',snapshot_id=self.snapshot['snapshot_id'],geometry=dict(n_angles=12,n_detectors=24,start_degrees=0,end_degrees=180),noise=dict(sigma=.01,seed=0));body.update(updates)
        response=self.client.post('/api/acquisitions',json=body);self.assertEqual(response.status_code,202,response.json)
        job=terminal(self.client,response.json['job_id']);self.assertEqual(job['status'],'completed',job)
        return job['result']
    def test_full_records_replay_cache_and_immutability(self):
        refs=self.acquire();refs2=self.acquire();acq=self.client.get('/api/acquisitions/'+refs['acquisition_id']).json
        acq2=self.client.get('/api/acquisitions/'+refs2['acquisition_id']).json
        self.assertTrue(acq2['operator_cache_hit']);self.assertTrue(acq2['clean_cache_hit'])
        y=self.client.get('/api/observations/'+refs['observation_id']).json
        y2=self.client.get('/api/observations/'+refs2['observation_id']).json
        self.assertEqual(y['y'],y2['y']);self.assertNotIn('y_clean',y);self.assertNotIn('truth',self.service.inference(refs['observation_id']))
        before=list(y['y']);y['y'][0]=99
        self.assertEqual(self.client.get('/api/observations/'+refs['observation_id']).json['y'],before)
        body=dict(request_id='run',observation_id=refs['observation_id'],iterations=201,step={'mode':'automatic_safe'})
        with patch('ct_editor.ct.service.sample_observation',side_effect=AssertionError('noise resampled')):
            submitted=self.client.post('/api/reconstructions',json=body);self.assertEqual(submitted.status_code,202,submitted.json)
            job=terminal(self.client,submitted.json['job_id']);self.assertEqual(job['status'],'completed',job)
            recid=job['result']['reconstruction_id'];record=self.client.get('/api/reconstructions/'+recid).json
            self.assertEqual(record['completed_iterations'],201);self.assertLessEqual(len(record['checkpoint_indices']),101)
            miss=self.client.get(f'/api/reconstructions/{recid}/checkpoints/2');self.assertEqual(miss.status_code,404)
            r=self.client.post(f'/api/reconstructions/{recid}/checkpoint-requests',json={'request_id':'replay','iteration':2});self.assertEqual(r.status_code,202)
            replayjob=terminal(self.client,r.json['job_id']);self.assertEqual(replayjob['status'],'completed',replayjob)
            cp=self.client.get(f'/api/reconstructions/{recid}/checkpoints/2').json
            op=self.service.cache[record['operator_id']][0]
            expected=landweber(op,np.array(before),record['configuration']['omega'],2)['final']
            np.testing.assert_array_equal(cp['pixels'],expected)
        self.assertEqual(self.client.get('/api/observations/'+refs['observation_id']).json['y'],before)
        for route in ('acquisitions','observations','reconstructions'):
            items=self.client.get('/api/'+route).json[route];self.assertNotIn('pixels',items[0]);self.assertNotIn('history',items[0]);self.assertNotIn('y',items[0]);self.assertNotIn('y_clean',items[0])
        h=self.client.get(f'/api/reconstructions/{recid}/history').json
        self.assertEqual(len(h['history']),202);self.assertEqual(len(h['diagnostics']['history']),202)
        # Evict cache, then rebuild from the frozen spec with the same checksum.
        with self.service.lock:
            for op,lease in self.service.cache.values(): lease.close()
            self.service.cache.clear();self.service.cache_bytes=0
        refs3=self.acquire();self.assertEqual(self.client.get('/api/acquisitions/'+refs3['acquisition_id']).json['operator']['csr_checksum'],acq['operator']['csr_checksum'])

    def test_invalid_inputs_and_capacity(self):
        basic=dict(request_id='bad',snapshot_id=self.snapshot['snapshot_id'],geometry=dict(n_angles=1,n_detectors=24,start_degrees=0,end_degrees=180),noise=dict(sigma=0,seed=0))
        for field,value in [('n_angles',True),('n_angles',1.5),('n_detectors',1),('start_degrees',180),('end_degrees',float('nan'))]:
            data={**basic,'geometry':{**basic['geometry'],field:value}}
            self.assertEqual(self.client.post('/api/acquisitions',json=data).status_code,400)
        for field,value in [('sigma',-1),('sigma',float('inf')),('seed',False),('seed',2**32)]:
            self.assertEqual(self.client.post('/api/acquisitions',json={**basic,'noise':{**basic['noise'],field:value}}).status_code,400)
        self.assertEqual(self.client.post('/api/acquisitions',json={**basic,'pixels':[]}).status_code,400)
        self.assertEqual(self.client.post('/api/acquisitions',json={**basic,'snapshot_id':'missing'}).status_code,404)
        self.service.limits['acquisitions']=0
        self.assertEqual(self.client.post('/api/acquisitions',json=basic).status_code,507)
        self.assertEqual(len(self.service.list('acquisitions')),0)
        self.assertEqual(self.client.get('/api/snapshots/'+self.snapshot['snapshot_id']).status_code,200)

    def test_cancel_build_is_atomic_and_releases_reservation(self):
        started=Event();release=Event();baseline=self.service.budget.used
        def interrupted(spec,check,progress):
            started.set();release.wait(3);check()
            raise AssertionError('Canceled construction continued')
        body=dict(request_id='cancel-build',snapshot_id=self.snapshot['snapshot_id'],
            geometry=dict(n_angles=12,n_detectors=24,start_degrees=0,end_degrees=180),noise=dict(sigma=0,seed=0))
        with patch('ct_editor.ct.service.build_operator',side_effect=interrupted):
            response=self.client.post('/api/acquisitions',json=body);self.assertEqual(response.status_code,202)
            self.assertTrue(started.wait(1));jid=response.json['job_id']
            self.client.post('/api/jobs/'+jid+'/cancel',json={});release.set()
            self.assertEqual(terminal(self.client,jid)['status'],'canceled')
        self.service.runner.close()
        self.assertEqual(self.service.budget.used,baseline)
        self.assertEqual(self.service.cache_bytes,0);self.assertFalse(self.service.cache)
        self.assertFalse(self.service.list('acquisitions'));self.assertFalse(self.service.list('observations'))
        self.assertTrue(all(count==0 for count in self.service.pending.values()))

    def test_partial_reconstruction_and_fractional_truth(self):
        refs=self.acquire(noise=dict(sigma=0,seed=0))
        acq=self.service._get('acquisitions',refs['acquisition_id'])
        op=self.service.cache[acq.meta()['operator_id']][0]
        truth=self.service.snapshots.array(self.snapshot['snapshot_id']).ravel()
        self.assertTrue(np.any(truth*255!=np.round(truth*255)))
        np.testing.assert_array_equal(acq.array('y_clean'),op.forward(truth))
        body=dict(request_id='partial',observation_id=refs['observation_id'],iterations=201,step={'mode':'automatic_safe'})
        for step in ({'mode':'manual','value':0},{'mode':'automatic_stopping'}):
            self.assertIn(self.client.post('/api/reconstructions',json={**body,'step':step}).status_code,(400,422))
        original=ImageDiagnostics.record
        def cancel_at_seven(diagnostics,k,x):
            original(diagnostics,k,x)
            if k==7:
                for job in self.service.runner.jobs.values():
                    if job.data['kind']=='reconstruction': job.event.set()
        with patch.object(ImageDiagnostics,'record',cancel_at_seven):
            response=self.client.post('/api/reconstructions',json=body)
            job=terminal(self.client,response.json['job_id'])
        self.assertEqual(job['status'],'canceled');recid=job['result']['reconstruction_id']
        record=self.service.result(recid)
        self.assertEqual(record['completed_iterations'],7);self.assertEqual(record['stop_reason'],'user_canceled')
        self.assertEqual(len(self.service.history(recid)['history']),8)
        self.assertIn(7,record['checkpoint_indices'])
        expected=landweber(op,acq.array('y_clean'),record['configuration']['omega'],7)['final']
        np.testing.assert_array_equal(record['pixels'],expected)

    def test_captured_input_queued_cancellation_and_resource_guard(self):
        hold=Event();started=Event()
        def block(job): started.set();hold.wait(3)
        blocking=self.service.runner.submit('test','block',{},block);started.wait(1)
        source_before=self.snapshot['pixels'][:]
        request=dict(request_id='queued',snapshot_id=self.snapshot['snapshot_id'],geometry=dict(n_angles=5,n_detectors=24,start_degrees=0,end_degrees=180),noise=dict(sigma=0,seed=0))
        result=self.client.post('/api/acquisitions',json=request).json
        request['geometry']['n_angles']=100
        self.assertEqual(self.client.get('/api/health').status_code,200)
        hold.set();job=terminal(self.client,result['job_id']);self.assertEqual(job['status'],'completed')
        acq=self.client.get('/api/acquisitions/'+job['result']['acquisition_id']).json;self.assertEqual(acq['shape'][0],5)
        self.assertEqual(self.client.get('/api/snapshots/'+self.snapshot['snapshot_id']).json['pixels'],source_before)
        original=self.service.budget.limit;self.service.budget.limit=self.service.budget.used+1
        response=self.client.post('/api/acquisitions',json=request);self.assertEqual(response.status_code,507);self.service.budget.limit=original


class JobTests(unittest.TestCase):
    def test_queue_cancel_and_progress(self):
        runner=JobRunner(pending_limit=1);hold=Event();started=Event();ran=[]
        def blocking(job): started.set();hold.wait(2);job.check()
        first=runner.submit('test','one',{},blocking);started.wait(1)
        queued=runner.submit('test','two',{},lambda job:ran.append(2))
        with self.assertRaises(InputError):runner.submit('test','three',{},lambda job:None)
        runner.cancel(queued['job_id']);self.assertEqual(runner.get(queued['job_id']).snapshot()['status'],'canceled')
        runner.cancel(first['job_id']);hold.set();runner.close();self.assertEqual(ran,[])
        self.assertEqual(runner.get(first['job_id']).snapshot()['status'],'canceled')

if __name__=='__main__': unittest.main()
