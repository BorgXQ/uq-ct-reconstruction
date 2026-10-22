import unittest
import numpy as np
from ct_editor.exact.calibration import source_norm
import test_exact


class SourceNormTests(unittest.TestCase):
    def test_full_source_norm_and_exponent(self):
        for nu in [.5,1.,2.]:
            result=source_norm(np.array([1.,4.]),np.array([3.,4.]),5.,nu,1e-12)
            self.assertEqual(result['status'],'compatible')
            expected=np.linalg.norm(np.array([3.,4.])/np.array([1.,4.])**nu)
            self.assertAlmostEqual(result['minimum_source_norm'],expected)
            self.assertGreaterEqual(result['M'],expected*(1-1e-15))
    def test_null_and_weak_directions_not_discarded(self):
        for value in [0.,1e-14]:
            result=source_norm(np.array([value,1.]),np.array([1.,1.]),2.,.5,1e-12)
            self.assertEqual(result['status'],'unresolved');self.assertIsNone(result['M'])
        self.assertEqual(source_norm(np.ones(2),np.ones(2),2.,.5,1e-12,1.)['status'],'incompatible')
        self.assertEqual(source_norm(np.zeros(2),np.zeros(2),0.,.5,1e-12)['M'],0)


class CalibrationServiceTests(unittest.TestCase):
    setUp=test_exact.ExactServiceTests.setUp
    tearDown=test_exact.ExactServiceTests.tearDown
    job=test_exact.ExactServiceTests.job
    def test_calibration_cached_for_exponent_and_independent_noise(self):
        scan=self.job('/api/acquisitions',dict(request_id='source-scan',snapshot_id=self.c.get('/api/acquisitions/'+self.aid).json['snapshot_id'],geometry=dict(n_angles=40,n_detectors=27,start_degrees=0,end_degrees=180),noise=dict(sigma=.1,seed=0)))
        self.aid=scan['result']['acquisition_id'];self.oid=scan['result']['observation_id']
        first=self.job('/api/source-calibrations',dict(request_id='source-a',observation_id=self.oid,nu=.5))['result']
        self.assertEqual(first['status'],'compatible');self.assertFalse(first['cache_hit'])
        second=self.job('/api/source-calibrations',dict(request_id='source-b',observation_id=self.oid,nu=1.))['result']
        self.assertTrue(second['cache_hit']);self.assertNotEqual(first['M'],second['M'])
        noise=self.job('/api/acquisitions/'+self.aid+'/observations',dict(request_id='source-noise',noise=dict(sigma=.1,seed=321)))
        third=self.job('/api/source-calibrations',dict(request_id='source-c',observation_id=noise['result']['observation_id'],nu=.5))['result']
        self.assertTrue(third['cache_hit']);self.assertEqual(first['M'],third['M'])
        bad=self.c.post('/api/source-calibrations',json=dict(request_id='bad-nu',observation_id=self.oid,nu=0))
        self.assertEqual(bad.status_code,400)
