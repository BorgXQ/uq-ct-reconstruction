"""Thread policy uses process-visible CPUs and restores library settings."""
import unittest
from unittest.mock import patch
import numpy as np
from threadpoolctl import threadpool_info, threadpool_limits
from ct_editor.ct.parallel import available_cpu_count, numerical_threads


class ParallelTests(unittest.TestCase):
    def test_affinity_and_fallback(self):
        with patch('ct_editor.ct.parallel.os.sched_getaffinity',return_value={2,3,4},create=True):
            self.assertEqual(available_cpu_count(),3)
        with patch('ct_editor.ct.parallel.os.sched_getaffinity',side_effect=OSError,create=True), patch('ct_editor.ct.parallel.os.cpu_count',return_value=None):
            self.assertEqual(available_cpu_count(),1)

    def test_blas_threads_and_restoration_on_error(self):
        np.eye(2) @ np.eye(2)  # Ensure the numerical library is loaded.
        with threadpool_limits(limits=1,user_api='blas'):
            with self.assertRaisesRegex(RuntimeError,'test error'):
                with patch('ct_editor.ct.parallel.available_cpu_count',return_value=2), numerical_threads():
                    pools=[p for p in threadpool_info() if p['user_api']=='blas']
                    self.assertTrue(pools)
                    self.assertTrue(all(p['num_threads']==2 for p in pools))
                    raise RuntimeError('test error')
            self.assertTrue(all(p['num_threads']==1 for p in threadpool_info() if p['user_api']=='blas'))
