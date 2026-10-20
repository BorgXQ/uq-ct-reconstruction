"""Native-bin forward regression against user-exported MATLAB R2026b arrays."""
from pathlib import Path
import unittest
import numpy as np
from scipy.io import loadmat
from ct_editor.ct.geometry import Geometry, build_operator, native_detector_offsets

ROOT=Path(__file__).resolve().parents[1]
REFERENCES=('matlab_radon_reference.mat','matlab_reference_modified_shepp_logan.mat',
            'matlab_reference_asymmetric_disks.mat')


class MatlabReferenceTests(unittest.TestCase):
    @unittest.skipUnless(all((ROOT/f).exists() for f in REFERENCES),
                         'Optional MATLAB reference exports are not installed')
    def test_three_native_sinograms_without_fitted_scale(self):
        g=Geometry.request(128,dict(n_angles=180,n_detectors=185,start_degrees=0,end_degrees=180))
        op=build_operator(g)
        for filename in REFERENCES:
            with self.subTest(filename=filename):
                reference=loadmat(ROOT/filename,squeeze_me=True)
                np.testing.assert_array_equal(g.offsets,reference['detector_positions'])
                np.testing.assert_allclose(g.angles,np.deg2rad(reference['theta'].astype(float)),atol=1e-15)
                actual=op.forward(reference['truth'].ravel()).reshape(180,185).T
                expected=reference['sinogram']
                np.testing.assert_allclose(actual,expected,rtol=1e-12,atol=2e-12)
                self.assertLess(np.linalg.norm(actual-expected)/np.linalg.norm(expected),1e-12)
                self.assertAlmostEqual(.1/np.sqrt(np.mean(actual**2)),float(reference['relative_noise_rms']),places=12)
        rng=np.random.default_rng(123)
        x=rng.normal(size=op.shape[1]);y=rng.normal(size=op.shape[0])
        np.testing.assert_allclose(op.forward(x)@y,x@op.adjoint(y),rtol=1e-12,atol=1e-10)

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
