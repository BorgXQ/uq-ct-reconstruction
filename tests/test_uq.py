"""Independent algebra, Monte Carlo, eligibility, and integration checks."""
import unittest
import numpy as np
from scipy.sparse import csr_matrix
from scipy.stats import norm
from app import app
from uq import experiment, observation, reconstruction
from uq.operators import apply_t, apply_t_transpose, apply_g, area_downsample
from uq.covariance import spectrum, finite_filter, covariance_diagonal, covariance_eigenvalues
from uq.bias import source_check, spectral_bound, global_bound, stopping_curve
from uq.confidence import marginal, joint_region
from uq.monte_carlo import validate


class OperatorTests(unittest.TestCase):
    def test_linear_identities_and_covariance(self):
        rng = np.random.default_rng(50)
        matrices = [rng.normal(size=(9,4)), np.array([[1.,2.,0.],[2.,4.,0.]])]
        for raw in matrices:
            a = csr_matrix(raw)
            values, v, tol = spectrum(a)
            omega = 1/(2*values[-1])
            g = np.eye(a.shape[1])-omega*(raw.T@raw)
            for k in (1,7,1000):
                t = omega*sum(np.linalg.matrix_power(g,j)@raw.T for j in range(k))
                y = rng.normal(size=a.shape[0])
                np.testing.assert_allclose(apply_t(a,y,k,omega),t@y,atol=1e-11)
                np.testing.assert_allclose(t@raw,np.eye(a.shape[1])-np.linalg.matrix_power(g,k),atol=1e-11)
                truth = rng.normal(size=a.shape[1])
                np.testing.assert_allclose(apply_t(a, a@truth, k, omega)-truth,
                                           -np.linalg.matrix_power(g,k)@truth, atol=1e-11)
                z = rng.normal(size=a.shape[1])
                np.testing.assert_allclose(apply_t_transpose(a,z,k,omega),t.T@z,atol=1e-11)
                np.testing.assert_allclose(apply_g(a,z,omega),g@z,atol=1e-12)
                diagonal,eta = covariance_diagonal(values,v,omega,k,.2)
                gamma = (v*eta)@v.T
                direct = .2**2*t@t.T
                np.testing.assert_allclose(gamma,direct,atol=1e-11)
                np.testing.assert_allclose(diagonal,np.diag(direct),atol=1e-11)
                np.testing.assert_allclose(gamma,gamma.T,atol=1e-12)
                self.assertGreaterEqual(np.linalg.eigvalsh(gamma).min(),-1e-12)
                self.assertTrue(np.all(diagonal>=0))

    def test_small_eigenvalues_and_oscillatory_filter(self):
        values=np.array([0.,1e-25,1.,1.5])
        for k in (1,2,1000):
            f=finite_filter(values,1.,k)
            direct=sum((1-values)**j for j in range(k))
            np.testing.assert_allclose(f,direct,rtol=1e-12)
            self.assertEqual(covariance_eigenvalues(values,1,k,.2)[0],0)

    def test_area_averaging(self):
        rng=np.random.default_rng(0)
        image=rng.random((256,256))
        for n in (16,24):
            reduced=area_downsample(image,n)
            self.assertAlmostEqual(float(reduced.mean()),float(image.mean()),places=13)
            np.testing.assert_allclose(area_downsample(np.ones((256,256))*.4,n),.4,atol=1e-14)
        np.testing.assert_allclose(area_downsample(image,16),image.reshape(16,16,16,16).mean(axis=(1,3)))


class StatisticalTests(unittest.TestCase):
    def setUp(self):
        self.a=csr_matrix(np.array([[1.,.2],[.1,2.],[.4,.1]]))
        self.values,self.v,self.tol=spectrum(self.a)
        self.omega=1/(2*self.values[-1])
        self.truth=np.array([.3,.7])

    def test_source_bounds_and_failures(self):
        a,v,tol=self.values,self.v,self.tol
        source=source_check(a,v,tol,self.truth,.5,1,'oracle')
        self.assertEqual(source['status'],'verified')
        for k in (1,10,1000):
            actual=np.linalg.norm(np.linalg.matrix_power(np.eye(2)-self.omega*(self.a.T@self.a).toarray(),k)@self.truth)
            spec=spectral_bound(a,self.omega,k,.5,source['M'])
            ub=global_bound(a[-1],k,.5,source['M'])
            self.assertLessEqual(actual,spec+1e-12)
            self.assertLessEqual(spec,ub+1e-12)
        self.assertEqual(source_check(a,v,tol,self.truth,.5,1e-12,'source')['status'],'failed')
        self.assertEqual(source_check(np.array([0.,1.]),np.eye(2),1e-10,np.array([1.,0.]),.5,10,'source')['status'],'failed')
        self.assertEqual(source_check(np.array([1e-10,1.]),np.eye(2),1e-10,np.array([0.,1.]),.5,10,'source')['status'],'unverified')

    def test_marginals_joint_and_conditioning(self):
        for level in (.9,.95,.99):
            st,r,lo,hi=marginal(np.array([0.,1.]),np.array([.2,.3]),.1,level)
            np.testing.assert_allclose(r,norm.ppf((1+level)/2)*np.array([.2,.3])+.1)
            self.assertLess(lo[0],0);self.assertGreater(hi[1],1)
        error=np.array([.2,-.1]);eta=np.array([.1,.2])
        j=joint_region(eta,self.v,error,.2,.95,2)
        gamma=(self.v*eta)@self.v.T
        self.assertAlmostEqual(j['statistic'],float(error@np.linalg.solve(gamma,error)))
        self.assertFalse(joint_region(np.array([0.,1.]),np.eye(2),error,.2,.95,1)['available'])
        self.assertFalse(joint_region(np.array([1e-12,1.]),np.eye(2),error,.2,.95,2)['available'])

    def test_monte_carlo_predictions_and_reproducibility(self):
        a,v=self.values,self.v;k=20;sigma=.1
        mean=apply_t(self.a,self.a@self.truth,k,self.omega)
        variance,eta=covariance_diagonal(a,v,self.omega,k,sigma)
        _,radius,_,_=marginal(mean,np.sqrt(variance),.02,.95)
        joint=joint_region(eta,v,mean-self.truth,.02,.95,2)
        args=(self.a,a,v,self.truth,mean,variance,self.omega,k,sigma,20000,123,radius,joint,eta)
        result=validate(*args)
        repeat=validate(*args)
        np.testing.assert_array_equal(result['empirical_mean'],repeat['empirical_mean'])
        self.assertLess(result['mean_error_norm'],4*result['mean_rmse_prediction'])
        self.assertLess(result['variance_relative_error'],.035)
        self.assertLess(abs(result['observed_mse']-result['predicted_mse']),4*result['mse_se'])
        self.assertGreater(result['coverage']['average'],.94)
        self.assertGreater(result['joint_coverage']['rate'],.94)
        np.testing.assert_allclose(result['empirical_bias'],result['empirical_mean']-self.truth)

    def test_stopping_first_crossing_and_no_crossing(self):
        s=stopping_curve(self.values,self.v,self.omega,.1,.5,1,1,1000,self.truth)
        found=[k for k,b,sd in zip(s['iterations'],s['bound'],s['max_sd']) if b<=sd]
        self.assertEqual(s['first'],found[0])
        self.assertTrue(all(b>sd for b,sd in zip(s['bound'][:s['first']-1],s['max_sd'][:s['first']-1])))
        self.assertIsNone(stopping_curve(self.values,self.v,self.omega,0,.5,1,1,20,self.truth)['first'])


class UqApiTests(unittest.TestCase):
    def setUp(self):
        self.client=app.test_client()
        self.body=dict(uq_method='classical',image=np.full((16,16),.4).tolist(),uq_resolution=16,angles=45,
                       iterations=10,budget=30,sigma=.02,seed=2,mode='oracle',trials=30)

    def test_full_workflow_and_dependency_invariance(self):
        response=self.client.post('/api/uq',json=self.body)
        self.assertEqual(response.status_code,200)
        r=response.json
        self.assertIsNotNone(r['maps']['lower'])
        self.assertLess(r['diagnostics']['mean_identity_error'],1e-10)
        alternate=self.client.post('/api/uq',json=self.body|{'iterations':20}).json
        self.assertEqual(r['measurements'],alternate['measurements'])
        self.assertEqual(r['stopping'],alternate['stopping'])
        alternate=self.client.post('/api/uq',json=self.body|{'seed':3}).json
        self.assertNotEqual(r['measurements']['noise'],alternate['measurements']['noise'])
        self.assertEqual(r['maps']['sd'],alternate['maps']['sd'])
        self.assertEqual(r['stopping'],alternate['stopping'])
        changed=self.client.post('/api/uq',json=self.body|{'sigma':.04}).json
        np.testing.assert_allclose(changed['maps']['sd'],2*np.array(r['maps']['sd']))
        self.assertEqual(r['maps']['mean'],changed['maps']['mean'])
        mc=self.client.post('/api/uq-mc',json=self.body).json
        self.assertEqual(mc['mc']['trials'],30)
        self.assertEqual(mc['maps'],r['maps'])
        self.assertIsNotNone(mc['mc']['coverage'])

    def test_ineligible_and_edge_cases(self):
        for changes in [{'mode':'variance'},{'mode':'source','M':1e-12},{'sigma':0},
                        {'step_mode':'phase1'},{'k_policy':'exploratory'}]:
            r=self.client.post('/api/uq',json=self.body|changes).json
            if changes.get('mode')=='variance' or changes.get('sigma')==0:
                self.assertIsNone(r['maps']['lower'])
                self.assertEqual(r['interval_status'],'unavailable')
            else:
                self.assertIsNotNone(r['maps']['lower'])
                self.assertEqual(r['interval_status'],'exploratory')
            self.assertIsNone(r['stopping'])
            self.assertFalse(r['joint']['available'])
            self.assertTrue(r['interval_reason'])
            if changes.get('step_mode')=='phase1':
                self.assertIsNone(r['bounds']['global_bound'])
                self.assertIsNotNone(r['bounds']['spectral'])
        truth=np.zeros((16,16));truth[2:4,10:12]=.8
        r=self.client.post('/api/uq',json=self.body|{'image':truth.tolist(),'angles':1}).json
        self.assertEqual(r['source']['status'],'failed')
        self.assertLess(r['rank'],r['pixels'])
        self.assertFalse(r['joint']['available'])
        r=self.client.post('/api/uq',json=self.body|{'image':np.zeros((24,24)).tolist(),'sigma':0,'uq_resolution':24,'iterations':1000}).json
        self.assertEqual(r['diagnostics']['error_norm'],0)
        self.assertEqual(np.max(r['maps']['sd']),0)

    def test_initial_iterate_has_matching_zero_uncertainty(self):
        r=self.client.post('/api/uq',json=self.body|{'iterations':0}).json
        self.assertTrue(np.all(np.array(r['maps']['reconstruction'])==0))
        self.assertTrue(np.all(np.array(r['maps']['sd'])==0))
        self.assertIsNone(r['maps']['lower'])
        self.assertFalse(r['joint']['available'])

    def test_selected_bound_and_actual_bias(self):
        r=experiment(self.body)
        self.assertEqual(r['bounds']['kind'],'spectral')
        self.assertEqual(r['interval_status'],'conditional')
        b=r['bounds']
        actual=np.linalg.norm(np.array(r['maps']['clean_reconstruction'])-np.array(self.body['image']))
        self.assertAlmostEqual(actual,r['diagnostics']['bias_norm'])
        self.assertLessEqual(actual,b['spectral']+1e-10)
        self.assertLessEqual(b['spectral'],b['global_bound']+1e-10)
        np.testing.assert_allclose(np.array(r['maps']['half_width']),np.array(r['maps']['stochastic'])+b['spectral'])
        from uq.covariance import geometry
        values=geometry(16,45)[4]
        expected=[spectral_bound(values,r['omega'],k,.5,r['source']['M']) for k in r['stopping']['iterations']]
        np.testing.assert_allclose(r['stopping']['bound'],expected)
        g=experiment(self.body|{'bias_bound':'global'})
        self.assertEqual(g['bounds']['selected'],g['bounds']['global_bound'])
        np.testing.assert_allclose(g['stopping']['bound'],global_bound(values[-1],np.arange(1,31),.5,g['source']['M']))

    def test_automatic_and_manual_stopping(self):
        body=self.body|{'iterations':100,'c0':100,'stopping_mode':'automatic'}
        for kind in ['spectral','global']:
            auto=self.client.post('/api/reconstruct',json=body|{'bias_bound':kind}).json
            r=auto['uncertainty']; first=r['stopping']['first']
            self.assertIsNotNone(first)
            self.assertEqual(r['config']['k'],first)
            self.assertEqual(auto['snapshots'][-1]['iteration'],first)
            np.testing.assert_allclose(auto['snapshots'][-1]['image'],r['maps']['reconstruction'])
            prior=first-1
            self.assertTrue(all(b>100*v for b,v in zip(r['stopping']['bound'][:prior],r['stopping']['max_sd'][:prior])))
            repeat=self.client.post('/api/reconstruct',json=body|{'bias_bound':kind,'seed':99}).json
            self.assertEqual(repeat['uncertainty']['config']['k'],first)
        manual=self.client.post('/api/reconstruct',json=body|{'stopping_mode':'manual'}).json
        self.assertEqual(manual['snapshots'][-1]['iteration'],100)
        capped=self.client.post('/api/reconstruct',json=body|{'iterations':1,'c0':.01}).json['uncertainty']
        self.assertIsNone(capped['stopping']['first'])
        self.assertEqual(capped['config']['k'],1)
        self.assertIn('no crossing',capped['selection']['message'])
        failed=self.client.post('/api/reconstruct',json=body|{'mode':'source','M':1e-12}).json['uncertainty']
        self.assertEqual(failed['interval_status'],'exploratory')
        self.assertIn('unavailable',failed['selection']['message'])
        self.assertEqual(failed['config']['k'],100)

    def test_unverified_source_never_claims_coverage(self):
        from unittest.mock import patch
        original=source_check
        def unverified(*args):
            result=original(*args);result.update(status='unverified',reason='Numerical compatibility is uncertain.')
            return result
        with patch('uq.source_check',side_effect=unverified):
            r=experiment(self.body)
        self.assertEqual(r['interval_status'],'exploratory')
        self.assertIsNotNone(r['maps']['lower'])
        self.assertIsNone(r['stopping'])
        self.assertFalse(r['joint']['available'])

    def test_large_iteration_runs_and_binary_stopping(self):
        from uq import spectral_history
        from uq.covariance import geometry
        from tomography import landweber
        a,_,_,_,values,vectors,_=geometry(16,45)
        truth=np.array(self.body['image']).ravel()
        omega=1/(2*values[-1])
        y=a@truth+np.random.default_rng(3).normal(0,.02,a.shape[0])
        direct=landweber(a,y,truth,1200,omega)
        fast=spectral_history(16,45,y,truth,1200,omega)
        np.testing.assert_allclose(fast['snapshots'][-1]['image'],direct['snapshots'][-1]['image'],atol=1e-10)
        for kind in ['spectral','global']:
            for budget in [2000,1000000]:
                curve=stopping_curve(values,vectors,omega,.02,.5,10,1,budget,truth,kind)
                self.assertLessEqual(len(curve['iterations']),102)
                k=curve['first']
                if budget==1000000:self.assertIsNotNone(k)
                def holds(j):
                    b=spectral_bound(values,omega,j,.5,10) if kind=='spectral' else global_bound(values[-1],j,.5,10)
                    sd=np.sqrt(np.max(covariance_diagonal(values,vectors,omega,j,.02)[0]))
                    return b<=sd
                if k is None:
                    self.assertFalse(holds(budget))
                else:
                    self.assertTrue(holds(k))
                    if k>1:self.assertFalse(holds(k-1))
        for mode in ['manual','automatic']:
            response=self.client.post('/api/reconstruct',json=self.body|{'iterations':1000000,'stopping_mode':mode})
            self.assertEqual(response.status_code,200)
            r=response.json;u=r['uncertainty']
            self.assertLessEqual(len(r['snapshots']),101)
            self.assertEqual(r['snapshots'][-1]['iteration'],u['config']['k'])
            if mode=='manual':self.assertEqual(u['config']['k'],1000000)
            else:self.assertEqual(u['config']['k'],u['stopping']['first'])
            np.testing.assert_allclose(r['snapshots'][-1]['image'],u['maps']['reconstruction'],atol=1e-10)
        self.assertTrue(np.isfinite(global_bound(values[-1],2**53-1,.5,10)))

    def test_validation(self):
        for changes in [{'uq_resolution':64},{'uq_resolution':17},{'sigma':float('nan')},{'sigma':1e-300},
                        {'sigma':-1},{'nu':0},{'M':0},{'budget':2**53},{'mode':'bad'},{'bias_bound':'bad'},{'stopping_mode':'bad'},
                        {'trials':501},{'mc_seed':-1},{'iterations':True}]:
            response=self.client.post('/api/uq-mc',json=self.body|changes)
            self.assertEqual(response.status_code,400,(changes,response.json))
