"""Regression reference: execute the standalone notebook's numerical functions."""
import ast
import contextlib
import io
import json
from pathlib import Path
import time
import unittest
import numpy as np
from numba import njit
from skimage.transform import radon
from app import app
from tomography import project, landweber


def notebook_functions(n, count):
    notebook = json.loads((Path(__file__).resolve().parents[1]/'section4_1_landweber.ipynb').read_text())
    namespace = dict(np=np, njit=njit, radon=radon, time=time, N=n,
                     THETA=np.linspace(0., 180., count, endpoint=False),
                     POWER_SEED=1, POWER_MAX_ITERATIONS=300, POWER_RTOL=1e-8)
    namespace['angles_radians'] = np.deg2rad(namespace['THETA'])
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code':
            continue
        tree = ast.parse(''.join(cell['source']))
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        exec(compile(tree, '<notebook reference>', 'exec'), namespace)
    return namespace


class NotebookParityTests(unittest.TestCase):
    def test_forward_noise_adjoint_step_and_iterations(self):
        for n, count in [(16, 180), (24, 17), (128, 180)]:
            with self.subTest(n=n, angles=count):
                ref = notebook_functions(n, count)
                truth = ref['shepp_logan'](n)
                a, y, theta, offsets, omega = project(truth, count, .1, 42)
                clean = ref['A'](truth)
                expected_y = clean + .1*np.random.default_rng(42).standard_normal(clean.shape)
                np.testing.assert_array_equal(y.reshape(clean.shape), expected_y)
                np.testing.assert_allclose(theta, ref['angles_radians'], atol=1e-15)
                np.testing.assert_array_equal(offsets, np.arange(clean.shape[0])-clean.shape[0]//2)
                np.testing.assert_array_equal(a.T @ y, ref['AT'](expected_y).ravel())
                largest, _, _ = ref['estimate_largest_eigenvalue']()
                self.assertAlmostEqual(omega/(1/(2*largest)), 1., places=13)
                with contextlib.redirect_stdout(io.StringIO()):
                    expected_x, residuals, _, _ = ref['landweber'](expected_y, 8, omega, truth)
                result = landweber(a, y, truth.ravel(), 8, omega)
                np.testing.assert_allclose(result['snapshots'][-1]['image'], expected_x, atol=1e-13)
                np.testing.assert_allclose([h['residual'] for h in result['history']], residuals, atol=1e-12)

    def test_api_matches_notebook_on_both_supported_grids(self):
        client = app.test_client()
        for n in (16, 24):
            ref = notebook_functions(n, 45)
            truth = ref['shepp_logan'](n)
            body = dict(image=truth.tolist(), angles=45, iterations=8, noise_std=.1,
                        noise_seed=42, mode='oracle', uq_method='classical')
            projected = client.post('/api/project', json=body)
            reconstructed = client.post('/api/reconstruct', json=body)
            self.assertEqual(projected.status_code, 200)
            self.assertEqual(reconstructed.status_code, 200)
            y = ref['A'](truth)
            y += .1*np.random.default_rng(42).standard_normal(y.shape)
            np.testing.assert_array_equal(projected.json['sinogram'], y)
            with contextlib.redirect_stdout(io.StringIO()):
                expected, _, _, _ = ref['landweber'](y, 8, projected.json['step'], truth)
            result = reconstructed.json
            np.testing.assert_allclose(result['snapshots'][-1]['image'], expected, atol=1e-13)
            np.testing.assert_allclose(result['uncertainty']['maps']['reconstruction'], expected, atol=1e-13)
            np.testing.assert_array_equal(result['uncertainty']['measurements']['observed'], y)
            # Legacy step spelling cannot reintroduce the old numerical pipeline.
            legacy = client.post('/api/reconstruct', json=body | {'step_mode':'phase1'})
            self.assertEqual(legacy.json, result)


if __name__ == '__main__':
    unittest.main()
