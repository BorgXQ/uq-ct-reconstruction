"""M5 deterministic statistics, real CT protocols, archives and replay."""
from copy import deepcopy
from io import BytesIO
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import numpy as np
from ct_editor import create_app
from ct_editor.validation.statistics import Statistics,wilson
from ct_editor.validation.engine import seed_words,observation
from ct_editor.validation.artifacts import read_bundle,encode_tree,load_array,SCHEMA
from ct_editor.ct.solver import landweber
from test_ct import terminal
from test_images import request_for
from test_exact import prepare


class StatisticsTests(unittest.TestCase):
    def test_welford_mse_identity_and_empty(self):
        s=Statistics(2);truth=np.array([1.,2.]);self.assertIsNone(s.result(3)[1]['mean'])
        for i,x in enumerate(([1.,2.],[3.,4.],[5.,6.])):
            s.add('completed_moments_only',truth,np.array(x),k=2)
            if i==0:self.assertIsNone(s.result(3)[1]['empirical_variance'])
        meta,a=s.result(3);np.testing.assert_allclose(a['mean'],[3,4]);np.testing.assert_allclose(a['empirical_variance'],[4,4]);np.testing.assert_allclose(a['mse'],[20/3]*2)
        np.testing.assert_allclose(a['mse'],2/3*a['empirical_variance']+(a['mean']-truth)**2)
    def test_denominators_wilson_and_joint(self):
        s=Statistics(2);truth=np.array([.5,.5]);h=np.array([.2,.2])
        for x in ([.5,.5],[.8,.5],[.5,.9],[.8,.9]):s.add('completed_interval',truth,np.array(x),h,k=2)
        s.add('criterion_not_met',truth);meta,a=s.result(5)
        np.testing.assert_equal(a['coverage'],[.5,.5]);np.testing.assert_equal(a['returned_and_covered'],[.4,.4]);np.testing.assert_allclose(a['mean_width'],.4);self.assertEqual(meta['all_pixel_inclusion'],.25)
        lo,hi,se=wilson(np.array([0,10]),10);self.assertGreater(hi[0],0);self.assertLess(lo[1],1);np.testing.assert_equal(se,[0,0])
    def test_seed_roles_and_batch_solver(self):
        self.assertEqual(seed_words(12,0,1,0),seed_words(12,0,1,0));self.assertNotEqual(seed_words(12,0,1,0),seed_words(12,0,1,1))
        self.assertNotEqual(seed_words(12,0,1,0),seed_words(12,1,1,0))
        op,sp,_=prepare(np.array([[1.]]));a=landweber(op,np.array([.5]),.5,2,retain_history=False);b=landweber(op,np.array([.5]),.5,2)
        np.testing.assert_array_equal(a['final'],b['final']);self.assertEqual(a['history'],[]);self.assertEqual(len(a['checkpoints']),1)
    def test_scalar_sampling_reference(self):
        op,sp,_=prepare(np.array([[1.]]));s=Statistics(1);h=np.array([1.959963984540054*.09375+.125])
        for i in range(2000):
            y=observation(np.array([.5]),.125,61,0,i);x=landweber(op,y,.5,2,retain_history=False)['final'];s.add('completed_interval',np.array([.5]),x,h,k=2)
        meta,a=s.result(2000);self.assertAlmostEqual(a['mean'][0],.375,delta=.008);self.assertAlmostEqual(a['empirical_variance'][0],9/1024,delta=.001)
        from scipy.stats import norm
        expected=norm.cdf((h[0]+.125)/.09375)-norm.cdf((-h[0]+.125)/.09375)
        self.assertAlmostEqual(a['coverage'][0],expected,delta=.02)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app({'TESTING':True,'VALIDATION_ARTIFACT_DIR':self.tmp.name,'VALIDATION_LIMIT':40})
        self.c=self.app.test_client();self.ct=self.app.extensions['ct_service'];self.v=self.app.extensions['validation_service']
        image=np.zeros((512,512),np.uint8);image[100:400,140:370]=128
        self.sid=self.c.post('/api/snapshots',json=request_for(image,16)).json['snapshot_id']
        scan=self.job('/api/acquisitions',dict(request_id='scan',snapshot_id=self.sid,geometry=dict(n_angles=12,n_detectors=24,start_degrees=0,end_degrees=180),noise=dict(sigma=.01,seed=3)))
        self.oid=scan['observation_id']
        req=dict(request_id='random',observation_id=self.oid,source=dict(nu=.5,M=2.,provenance='test declaration'),alpha=.05,
            settings_origin='declared_independent',declaration='Synthetic test',step_policy='safe_induced_norm_v1',selection=dict(mode='fixed',iterations=4,provenance='fixed_predeclared'),
            rank=2,oversampling=4,sketch_seed=17,probe_seed=23,probe_count=4,block_size=2,epsilon=.2,delta=.01,eta=dict(value=.1,provenance='test'),backend='matrix_free',reference=False)
        self.template=self.job('/api/randomized-analyses',req)['analysis_id']
    def tearDown(self):self.ct.runner.close();self.tmp.cleanup()
    def job(self,path,data):
        resp=self.c.post(path,json=data);self.assertEqual(resp.status_code,202,resp.json);j=terminal(self.c,resp.json['job_id']);self.assertEqual(j['status'],'completed',j);return j['result']
    def request(self,**updates):
        d=dict(request_id='validation',template_id=self.template,protocol='fixed_algorithm',comparison='common_k',trials=4,root_seed=44,
            methods=['exact','tsvd','retained','corrected'],iterations=4,budget=6,pixels=[0,19],representatives=[0,2],diagnostics='representative',reference=True,diagnostic_iterations=[1,4])
        d.update(updates);return d
    def study(self,**updates):return self.v.get(self.job('/api/validation-studies',self.request(**updates))['study_id'])
    def test_fixed_shared_centers_and_reproducibility(self):
        r=self.study();self.assertEqual(r['completed_trials'],4);self.assertEqual(len(r['ledger']),16)
        for m in r['plan']['methods']:
            self.assertEqual(r['summaries'][m]['returned_intervals'],4)
            np.testing.assert_array_equal(r['maps'][m]['mean'],r['maps']['exact']['mean'])
            np.testing.assert_array_equal(r['representatives']['0_'+m]['center'],r['representatives']['0_exact']['center'])
        self.assertEqual(len(set(row['factor_id'] for row in r['ledger'])),1)
        self.assertFalse(np.array_equal(r['representatives']['0_corrected']['y'],r['representatives']['2_corrected']['y']))
        np.testing.assert_array_equal(r['representatives']['0_corrected']['half_width'],r['representatives']['2_corrected']['half_width'])
        self.assertIn('mean_reference',r['maps']['corrected']);self.assertEqual(len(r['bias_curve']),2)
        again=self.study();np.testing.assert_array_equal(r['maps']['corrected']['mean'],again['maps']['corrected']['mean'])
        self.assertEqual(len(self.ct.records['observations']),1)
    def test_all_random_changes_only_interval_at_common_k(self):
        r=self.study(protocol='all_random_sources',methods=['retained','corrected'],reference=False,diagnostic_iterations=[])
        self.assertEqual(len(set(row['factor_id'] for row in r['ledger'])),4)
        for i in range(4):self.assertEqual(r['ledger'][2*i]['factor_id'],r['ledger'][2*i+1]['factor_id'])
        np.testing.assert_array_equal(r['maps']['retained']['mean'],r['maps']['corrected']['mean'])
        again=self.study(protocol='all_random_sources',methods=['retained','corrected'],reference=False,diagnostic_iterations=[])
        np.testing.assert_allclose(r['maps']['corrected']['mean_width'],again['maps']['corrected']['mean_width'])
    def test_matrix_free_and_missing_reference(self):
        with patch.object(self.v.exact,'spectrum',side_effect=AssertionError('hidden full SVD')):
            r=self.study(methods=['corrected'],reference=False,diagnostic_iterations=[])
        self.assertIsNone(r['maps']['corrected']['full_variance']);self.assertEqual(r['source_check']['status'],'unavailable')
    def test_no_crossing_and_mixture(self):
        r=self.study(protocol='all_random_sources',comparison='own_stopping',budget=1,methods=['corrected'],reference=False,diagnostic_iterations=[])
        self.assertEqual(r['summaries']['corrected']['outcomes'],{'criterion_not_met':4});self.assertIsNone(r['maps']['corrected']['coverage']);self.assertNotIn('full_variance',r['maps']['corrected']);self.assertIn('mixture',r['conditioning'])
    def test_plan_validation_and_limits(self):
        for updates in [dict(trials=True),dict(pixels=[9999]),dict(methods=[]),dict(trials=10001),dict(mc_alpha=0)]:
            response=self.c.post('/api/validation-studies',json=self.request(**updates));self.assertEqual(response.status_code,400,response.json)
        with patch.object(self.v,'max_work',1):self.assertEqual(self.c.post('/api/validation-studies/preflight',json=self.request()).status_code,507)
    def test_bundle_restore_restart_and_replay(self):
        r=self.study(trials=2,representatives=[0],methods=['corrected']);sid=r['study_id']
        exp=self.job('/api/exports',dict(request_id='export',study_id=sid,profile='numerical'));response=self.c.get(exp['download']);raw=response.data;response.close()
        manifest,loaded=read_bundle(raw,512*1024**2);np.testing.assert_array_equal(loaded['maps']['corrected']['mean'],r['maps']['corrected']['mean'])
        self.ct.runner.close();app=create_app({'TESTING':True,'VALIDATION_ARTIFACT_DIR':self.tmp.name});client=app.test_client()
        try:
            response=client.post('/api/imports',data=raw,content_type='application/zip');self.assertEqual(response.status_code,202,response.json)
            done=terminal(client,response.json['job_id']);self.assertEqual(done['status'],'completed',done);iid=done['result']['import_id']
            replay=client.post('/api/imports/'+iid+'/replay-requests',json=dict(mode='study',request_id='replay'));done=terminal(client,replay.json['job_id']);self.assertEqual(done['status'],'completed',done);self.assertEqual(done['result']['status'],'agreement',done)
            trial=client.post('/api/imports/'+iid+'/replay-requests',json=dict(mode='trial',trial=0,request_id='trial'));done=terminal(client,trial.json['job_id']);self.assertEqual(done['result']['status'],'agreement',done)
        finally:app.extensions['ct_service'].runner.close()
    def test_archive_attacks_and_npy_header(self):
        for name in ('../escape','/absolute','a\\b','a/../b'):
            buf=BytesIO()
            with zipfile.ZipFile(buf,'w') as z:z.writestr(name,'bad')
            with self.assertRaises(ValueError):read_bundle(buf.getvalue(),100000)
        buf=BytesIO();np.save(buf,np.array([object()]),allow_pickle=True)
        with self.assertRaises(ValueError):load_array(buf.getvalue())
        r=self.study(trials=1,representatives=[0],methods=['corrected']);exp=self.job('/api/exports',dict(request_id='export',study_id=r['study_id'],profile='numerical'));response=self.c.get(exp['download']);raw=response.data;response.close()
        out=BytesIO()
        with zipfile.ZipFile(BytesIO(raw)) as source,zipfile.ZipFile(out,'w') as target:
            for name in source.namelist():target.writestr(name,source.read(name)+b' ' if name=='study.json' else source.read(name))
        with self.assertRaises(ValueError):read_bundle(out.getvalue(),512*1024**2)
    def test_parameter_grid_and_figures(self):
        base=self.request(trials=1,representatives=[0],methods=['corrected'],diagnostic_iterations=[])
        b=dict(request_id='grid',base=base,axis='rank',values=[2,4]);pre=self.c.post('/api/parameter-studies/preflight',json=b);self.assertEqual(pre.status_code,200,pre.json)
        b['accepted_hash']=pre.json['accepted_hash'];gid=self.job('/api/parameter-studies',b)['grid_id'];g=self.c.get('/api/parameter-studies/'+gid).json;self.assertEqual(len(g['rows']),2)
        exp=self.job('/api/exports',dict(request_id='figures',study_id=g['rows'][0]['study_id'],profile='figures'));response=self.c.get(exp['download']);raw=response.data;response.close()
        with zipfile.ZipFile(BytesIO(raw)) as z:
            pngs=[name for name in z.namelist() if name.endswith('.png')];self.assertGreater(len(pngs),3);self.assertTrue(z.read(pngs[0]).startswith(b'\x89PNG'))
    def test_missing_declarations_moments_and_failed_diagnostics_kept(self):
        from ct_editor.images import InputError
        with patch('ct_editor.validation.engine.bias_diagnostics',side_effect=InputError('Injected optional diagnostic failure')):
            r=self.study(trials=2,representatives=[0],methods=['corrected'],diagnostics='all',diagnostic_iterations=[])
        self.assertEqual(r['summaries']['corrected']['returned_intervals'],2)
        self.assertEqual(r['summaries']['corrected']['diagnostics']['unavailable'],2)
        p,c,spec,truth,step=self.v.capture(self.request(methods=['corrected'],reference=False,diagnostic_iterations=[]))
        c['source']=None;c['eta']=None;p['scope']='moments'
        from ct_editor.validation.service import validate_plan
        validate_plan(p,c,len(truth))
        from ct_editor.ct.jobs import Job
        rr=self.v.run('moments',p,c,spec,truth,step,Job('test','test',{},None,lambda:None))
        self.assertEqual(rr['summaries']['corrected']['numeric_centers'],4);self.assertIsNone(rr['maps']['corrected']['coverage'])
    def test_parameter_archive_and_single_result_profiles(self):
        b=dict(request_id='grid',base=self.request(trials=1,representatives=[0],methods=['corrected'],diagnostic_iterations=[]),axis='sigma',values=[0,.01])
        pre=self.c.post('/api/parameter-studies/preflight',json=b).json;b['accepted_hash']=pre['accepted_hash']
        gid=self.job('/api/parameter-studies',b)['grid_id'];e=self.job('/api/exports',dict(request_id='gridexport',study_id=gid,profile='parameter_study'))
        response=self.c.get(e['download']);raw=response.data;response.close();manifest,r=read_bundle(raw,512*1024**2)
        self.assertEqual(len(r['studies']),2)
        response=self.c.post('/api/imports',data=raw,content_type='application/zip');done=terminal(self.c,response.json['job_id']);self.assertEqual(done['status'],'completed',done);self.assertEqual(len(done['result']['study_ids']),2)
        sid=r['studies'][0]['study_id'];e=self.job('/api/exports',dict(request_id='single',study_id=sid,profile='single_result',trial=0));response=self.c.get(e['download']);raw=response.data;response.close();manifest,_=read_bundle(raw,512*1024**2);self.assertEqual(manifest['selected_trial'],0)
    def test_failure_ledger_and_partial_archive(self):
        from ct_editor.images import InputError
        with patch('ct_editor.validation.engine.solve',side_effect=InputError('Injected solve failure',code='numerical_range')):
            r=self.study(trials=2,representatives=[0],methods=['corrected'],diagnostics='none',diagnostic_iterations=[])
        self.assertEqual(r['summaries']['corrected']['outcomes'],{'numerical_failure':2});self.assertEqual(r['summaries']['corrected']['terminal'],2)
        self.assertIsNone(r['maps']['corrected']['mean'])
    def test_fresh_own_stopping_replay(self):
        r=self.study(trials=3,representatives=[0],methods=['retained','corrected'],protocol='all_random_sources',comparison='own_stopping',budget=200,diagnostic_iterations=[])
        self.assertEqual(r['summaries']['corrected']['returned_intervals'],3)
        replay=self.job('/api/validation-studies/'+r['study_id']+'/inspection-requests',dict(mode='study',request_id='freshreplay'))
        self.assertEqual(replay['status'],'agreement',replay)
    def test_archive_symlink_duplicates_and_export_capacity(self):
        import stat
        for mode in ('duplicate','symlink','compression'):
            raw=BytesIO()
            with zipfile.ZipFile(raw,'w',compression=zipfile.ZIP_DEFLATED) as z:
                if mode=='duplicate':
                    z.writestr('A.json','x');z.writestr('a.json','y')
                elif mode=='symlink':
                    info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,'/tmp/target')
                else:z.writestr('bomb',b'0'*(2*1024**2))
            with self.assertRaises(ValueError):read_bundle(raw.getvalue(),8*1024**2)
        r=self.study(trials=1,representatives=[0],methods=['corrected'],diagnostic_iterations=[])
        before=len(self.app.extensions['validation_artifacts'].exports)
        with patch.object(self.v,'disk_limit',1):
            submitted=self.c.post('/api/exports',json=dict(study_id=r['study_id'],profile='numerical',request_id='disk-full'))
            done=terminal(self.c,submitted.json['job_id']);self.assertEqual(done['status'],'failed');self.assertEqual(done['error']['code'],'storage_limit')
        self.assertEqual(len(self.app.extensions['validation_artifacts'].exports),before);self.assertIn(r['study_id'],self.v.records)
    def test_zero_noise_and_zero_truth(self):
        from ct_editor.ct.jobs import Job
        p,c,spec,truth,step=self.v.capture(self.request(methods=['corrected'],reference=False,diagnostic_iterations=[],representatives=[0]))
        c['sigma']=0;truth=np.zeros_like(truth)
        r=self.v.run('zero',p,c,spec,truth,step,Job('test','test',{},None,lambda:None))
        np.testing.assert_array_equal(r['maps']['corrected']['empirical_variance'],np.zeros_like(truth));self.assertEqual(r['summaries']['corrected']['mean_coverage'],1)
    def test_pixel_replay_and_cancel_prefix(self):
        r=self.study(trials=2,representatives=[0],methods=['corrected'],diagnostic_iterations=[])
        d=self.v.pixel(r['study_id'],7);self.assertIsNone(d['methods']['corrected']['series'])
        refs=self.job('/api/validation-studies/'+r['study_id']+'/inspection-requests',dict(request_id='pixel',mode='pixel',pixel=7));new=self.v.get(refs['replay_study_id']);self.assertEqual(len(new['series']['corrected']['7']),2)
        from ct_editor.ct.jobs import Job
        p,c,spec,truth,step=self.v.capture(self.request(trials=5,methods=['corrected'],reference=False,diagnostic_iterations=[]))
        job=Job('test','test',{},None,lambda:None)
        def progress(stage,done,total):
            if stage=='Committed validation trials' and done==2:job.event.set()
        job.progress=progress
        rr=self.v.run('partial',p,c,spec,truth,step,job);self.assertEqual(rr['status'],'partial_canceled');self.assertEqual(rr['completed_trials'],2);self.assertEqual(rr['summaries']['corrected']['not_started'],3)
