"""Self-contained covariance regression for the MATLAB-style projector."""
import unittest
import numpy as np
from ct_editor.ct.geometry import Geometry, build_operator, native_detector_offsets


class ProjectorCovarianceTests(unittest.TestCase):
    def test_finite_k_covariance_uses_new_operator_transpose(self):
        # Independent dense polynomial reference for the propagated covariance.
        from ct_editor.ct.solver import resolve_step,landweber
        op=build_operator(Geometry.arrays(3,[0,.5,1.2],native_detector_offsets(3)))
        a=op.sparse_copy().toarray();step=resolve_step(op,{'mode':'automatic_safe'})['omega']
        k=7;sigma=.1;g=np.eye(9)-step*a.T@a
        t=step*sum((np.linalg.matrix_power(g,j) for j in range(k)))@a.T
        propagated=np.column_stack([landweber(op,np.eye(a.shape[0])[:,j],step,k,retain_history=False)['final']
                                    for j in range(a.shape[0])])
        np.testing.assert_allclose(propagated,t,atol=1e-14)
        np.testing.assert_allclose(sigma**2*propagated@propagated.T,sigma**2*t@t.T,atol=1e-15)
