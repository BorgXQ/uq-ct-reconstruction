"""Independent checks of §3.6 formulas, omitted-bias audits, and stopping."""
import unittest
import numpy as np
from scipy.stats import norm
from app import app
from uq import experiment, settings
from uq.bias import source_check
from uq.covariance import geometry
from uq.practical import Practical, approximation


class PracticalTests(unittest.TestCase):
    def setUp(self):
        self.body = dict(image=np.full((16,16),.4).tolist(), angles=45,
                         iterations=80, budget=300, sigma=.02, mode='oracle',
                         retained_rank=32, probes=100, tail_eta=.1)

    def model(self, **changes):
        truth, c = settings(self.body | changes)
        _, _, _, _, vals, vecs, tol = geometry(c['n'], c['angles'])
        source = source_check(vals,vecs,tol,truth,c['nu'],c['m'],c['mode'])
        return Practical(c,truth,source)

    def test_randomized_operator_and_residual_against_dense_actions(self):
        p=self.model()
        lam,u,_,_,_,_=approximation(16,45,32,10,17,100,23)
        np.testing.assert_allclose(u.T@u,np.eye(32),atol=1e-12)
        a=geometry(16,45)[0].toarray();b=a.T@a
        np.testing.assert_allclose(u.T@b@u,np.diag(lam),atol=1e-11)
        g=np.eye(256)-p.omega*b
        t=p.omega*sum(np.linalg.matrix_power(g,j)@a.T for j in range(5))
        exact=.02**2*t@t.T
        gt=np.eye(256)-p.omega*((u*lam)@u.T)
        sums=sum(np.linalg.matrix_power(gt,j) for j in range(5))
        approximate=.02**2*p.omega**2*sums@((u*lam)@u.T)@sums
        w=np.random.default_rng(np.random.SeedSequence([23,9281])).choice([-1.,1.],size=(256,100))
        expected=np.mean(((exact-approximate)@w)*w,axis=1)
        bias,diag,estimate,upper=p.evaluate(5)
        np.testing.assert_allclose(diag,np.diag(approximate),atol=1e-14)
        np.testing.assert_allclose(estimate,expected,atol=1e-14)
        np.testing.assert_allclose(upper,diag+np.abs(expected)/.9,atol=1e-14)
        self.assertAlmostEqual(bias,p.source['M']*max((1-p.omega*lam)**5*lam**.5))

    def test_stopping_matches_exhaustive_search_and_large_limit(self):
        for rank in [16,64,128]:
            p=self.model(retained_rank=rank)
            hits=[k for k in range(1,401) if p.evaluate(k)[0]<=np.sqrt(max(p.evaluate(k)[3]))]
            first=hits[0] if hits else None
            self.assertEqual(p.stopping(400)['first'],first)
            if first is not None:self.assertEqual(p.stopping(1000000)['first'],first)
            # Check the search's pruning bound against actual values inside.
            ceiling=p.interval_sd_ceiling(20,100)
            for k in [20,30,70,100]:self.assertLessEqual(np.sqrt(max(p.evaluate(k)[3])),ceiling+1e-12)

    def test_full_rank_matches_classical_and_low_rank_flags_failures(self):
        full=experiment(self.body|{'retained_rank':256,'tail_eta':0})
        classical=experiment(self.body|{'uq_method':'classical'})
        np.testing.assert_allclose(full['maps']['reconstruction'],classical['maps']['reconstruction'],atol=1e-12)
        np.testing.assert_allclose(full['maps']['lower'],classical['maps']['lower'],atol=1e-10)
        self.assertEqual(full['interval_status'],'conditional')
        self.assertTrue(full['practical']['tail_pass'])
        low=experiment(self.body|{'iterations':200,'tail_eta':0})
        self.assertEqual(low['interval_status'],'exploratory')
        self.assertFalse(low['practical']['tail_pass'])
        self.assertIsNotNone(low['maps']['lower'])
        self.assertIn('Omitted bias',low['interval_reason'])
        np.testing.assert_allclose(low['maps']['stochastic'],norm.ppf(.975)*np.sqrt(low['maps']['corrected_variance']))
        np.testing.assert_allclose(low['maps']['half_width'],np.array(low['maps']['stochastic'])+low['bounds']['selected'])

    def test_independent_seeds_and_actual_bias_decomposition(self):
        p=self.model();r=experiment(self.body)
        repeated=experiment(self.body)
        self.assertEqual(r,repeated)
        noise=experiment(self.body|{'seed':99})
        self.assertEqual(r['stopping'],noise['stopping'])
        self.assertEqual(r['practical'],noise['practical'])
        self.assertNotEqual(r['maps']['reconstruction'],noise['maps']['reconstruction'])
        sketch=experiment(self.body|{'sketch_seed':99})
        self.assertEqual(r['maps']['reconstruction'],sketch['maps']['reconstruction'])
        self.assertNotEqual(r['maps']['approximate_variance'],sketch['maps']['approximate_variance'])
        a=geometry(16,45)[0].toarray();g=np.eye(256)-p.omega*(a.T@a)
        projected=p.u@(p.u.T@p.truth);gk=np.linalg.matrix_power(g,80)
        self.assertAlmostEqual(r['practical']['retained_bias_norm'],np.linalg.norm(gk@projected))
        self.assertAlmostEqual(r['practical']['omitted_bias_norm'],np.linalg.norm(gk@(p.truth-projected)))

    def test_api_auto_mc_and_parameter_validation(self):
        client=app.test_client()
        body=self.body|{'iterations':1000,'stopping_mode':'automatic'}
        response=client.post('/api/reconstruct',json=body)
        self.assertEqual(response.status_code,200)
        r=response.json['uncertainty'];k=r['stopping']['first']
        self.assertEqual(r['config']['k'],k)
        self.assertEqual(response.json['snapshots'][-1]['iteration'],k)
        np.testing.assert_allclose(response.json['snapshots'][-1]['image'],r['maps']['reconstruction'],atol=1e-12)
        mc=client.post('/api/uq-mc',json=body|{'iterations':k,'budget':1000,'trials':20}).json
        self.assertEqual(mc['maps'],r['maps'])
        self.assertEqual(mc['practical'],r['practical'])
        self.assertIsNotNone(mc['mc']['coverage'])
        for change in [{'retained_rank':257},{'retained_rank':0},{'oversampling':3},
                       {'probes':0},{'probes':10001},{'probe_epsilon':1},{'probe_delta':0},
                       {'tail_eta':-1},{'tail_eta':float('nan')},{'sketch_seed':-1},
                       {'probe_seed':True},{'uq_method':'bad'}]:
            self.assertEqual(client.post('/api/uq',json=self.body|change).status_code,400,change)
        for change in [{'sigma':0},{'k_policy':'exploratory'},{'mode':'source','M':1e-12}]:
            x=experiment(self.body|change)
            self.assertNotEqual(x['interval_status'],'conditional')


if __name__=='__main__':unittest.main()
