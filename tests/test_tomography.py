import unittest
import numpy as np
from tomography import build_operator, normalize_image, project, landweber
from app import app


class NumericalTests(unittest.TestCase):
    def test_adjoint(self):
        rng = np.random.default_rng(12)
        for n, count in [(64, 90), (128, 17), (256, 7)]:
            a, _, _, _ = build_operator(n, count)
            x, z = rng.normal(size=a.shape[1]), rng.normal(size=a.shape[0])
            self.assertAlmostEqual(float((a @ x) @ z), float(x @ (a.T @ z)), places=10)

    def test_library_geometry_scale_and_orientation(self):
        from skimage.transform import radon
        for n, count in [(16, 180), (24, 7), (128, 17)]:
            a, angles, offsets, _ = build_operator(n, count)
            image = np.ones((n, n))
            image[2:7, n-9:n-3] = .2
            expected = radon(image, theta=np.rad2deg(angles), circle=False, preserve_range=True)
            actual = (a @ image.ravel()).reshape(len(offsets), count)
            np.testing.assert_allclose(actual, expected, atol=1e-12)
            self.assertEqual(len(offsets), int(np.ceil(np.sqrt(2)*n)))
            self.assertEqual(offsets[len(offsets)//2], 0)
            # At zero degrees the detector values are column sums, in pixel units.
            np.testing.assert_allclose(actual[len(offsets)//2-n//2:len(offsets)//2+n//2, 0],
                                       image.sum(axis=0), atol=1e-12)

    def test_small_uq_matrix_matches_library_and_transpose(self):
        rng = np.random.default_rng(31)
        for n in (16, 24):
            a, _, _, _ = build_operator(n, 17)
            x, y = rng.normal(size=a.shape[1]), rng.normal(size=a.shape[0])
            np.testing.assert_allclose(a.sparse @ x, a @ x, atol=1e-12)
            np.testing.assert_allclose(a.sparse.T @ y, a.T @ y, atol=1e-12)

    def test_density_preserved(self):
        image = normalize_image(np.full((16, 16), 127 / 255))
        self.assertEqual(image[0, 0], 127 / 255)
        for invalid in [np.zeros((12, 12)), np.full((16, 16), np.nan), np.full((16, 16), 1.1)]:
            with self.assertRaises(ValueError):
                normalize_image(invalid)

    def test_convergence_quality_and_step_bound(self):
        n = 64
        yy, xx = np.mgrid[:n, :n]
        truth = (((xx-39)**2 + (yy-23)**2) < 10**2).astype(float) * .8
        truth[40:50, 12:23] = .4
        a, y, _, _, step = project(truth, 90)
        result = landweber(a, y, truth.ravel(), 200, step)
        objective = np.array([v['residual']**2 / 2 for v in result['history']])
        self.assertTrue(np.all(np.diff(objective) <= 1e-10))
        self.assertLess(result['history'][-1]['error'], .15)
        self.assertLess(objective[-1], objective[0] * .001)
        self.assertEqual(result['snapshots'][0]['iteration'], 0)
        self.assertEqual(result['snapshots'][-1]['iteration'], 200)
        # Bound is independently checked against a dense small-geometry spectrum.
        small, _, _, omega = build_operator(16, 2)
        largest_squared = np.linalg.norm(small.sparse.toarray(), 2)**2
        self.assertLess(omega, 2/largest_squared)

    def test_gaussian_noise(self):
        truth = np.full((64, 64), .4)
        a, clean, _, _, step = project(truth, 90)
        _, noisy, _, _, _ = project(truth, 90, .05, 123)
        np.testing.assert_array_equal(project(truth, 90, 0, 999)[1], clean)
        np.testing.assert_array_equal(project(truth, 90, .05, 123)[1], noisy)
        self.assertFalse(np.array_equal(project(truth, 90, .05, 124)[1], noisy))
        noise = noisy - clean
        self.assertAlmostEqual(float(noise.mean()), 0, delta=.002)
        self.assertAlmostEqual(float(noise.std()), .05, delta=.002)
        result = landweber(a, noisy, truth.ravel(), 15, step)
        self.assertTrue(np.all(np.diff([v['residual'] for v in result['history']]) <= 1e-10))
        blank = project(np.zeros((64,64)), 90, .05, 123)[1]
        self.assertLess(blank.min(), 0)
        self.assertGreater(blank.max(), 0)

    def test_larger_grid_snapshot_budget(self):
        for n, expected in [(16, 101), (24, 101)]:
            truth = normalize_image(np.full((n,n), .4))
            a, y, _, _, step = project(truth, 2)
            result = landweber(a, y, truth.ravel(), 100, step)
            self.assertEqual(len(result['snapshots']), expected)
            self.assertEqual(result['snapshots'][-1]['iteration'], 100)
            self.assertEqual(np.shape(result['snapshots'][-1]['image']), (n,n))

    def test_zero_image(self):
        truth = np.zeros((64, 64))
        a, y, _, _, step = project(truth, 12)
        result = landweber(a, y, truth.ravel(), 5, step)
        self.assertTrue(all(h['residual'] == 0 and h['error'] is None for h in result['history']))
        np.testing.assert_array_equal(result['snapshots'][-1]['image'], truth)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.body = {'image':np.full((16, 16), .4).tolist(), 'angles':12, 'iterations':4}

    def test_page_and_workflow(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        projected = self.client.post('/api/project', json=self.body)
        self.assertEqual(projected.status_code, 200)
        self.assertEqual(len(projected.json['sinogram'][0]), 12)
        reconstructed = self.client.post('/api/reconstruct', json=self.body)
        self.assertEqual(reconstructed.status_code, 200)
        self.assertEqual(len(reconstructed.json['history']), 5)
        self.assertEqual(reconstructed.json['step'], projected.json['step'])

    def test_noisy_measurements_match_reconstruction(self):
        body = self.body | {'noise_std':.1, 'noise_seed':42}
        projected = self.client.post('/api/project', json=body).json
        result = self.client.post('/api/reconstruct', json=body).json
        self.assertAlmostEqual(result['history'][0]['residual'], np.linalg.norm(projected['sinogram']))

    def test_shared_resolution_and_noise(self):
        for n in (16,24):
            for step_mode in ('paper','phase1'):
                body=self.body | {'image':np.full((n,n),127/255).tolist(), 'noise_std':.02, 'step_mode':step_mode}
                projected=self.client.post('/api/project',json=body).json
                reconstructed=self.client.post('/api/reconstruct',json=body).json
                uncertainty=reconstructed['uncertainty']
                np.testing.assert_array_equal(projected['sinogram'],uncertainty['measurements']['observed'])
                np.testing.assert_allclose(reconstructed['snapshots'][-1]['image'],uncertainty['maps']['reconstruction'],atol=1e-14)
                self.assertEqual(projected['step'],uncertainty['omega'])
                self.assertEqual(reconstructed['step'],uncertainty['omega'])
                self.assertEqual(uncertainty['config']['n'],n)
        for n in (32,64,128,256):
            self.assertEqual(self.client.post('/api/project',json=self.body|{'image':np.zeros((n,n)).tolist()}).status_code,400)

    def test_bad_inputs(self):
        for change in [{'noise_std':-1}, {'noise_std':1.1}, {'noise_std':True}, {'noise_std':'0.1'}, {'noise_std':float('nan')}, {'noise_seed':-1}, {'noise_seed':1.5}, {'noise_seed':True}, {'image':np.zeros((32,32)).tolist()}, {'angles':0}, {'angles':181}, {'angles':2.5}, {'angles':True}, {'iterations':2**53}, {'image':[]}]:
            self.assertEqual(self.client.post('/api/reconstruct', json=self.body | change).status_code, 400)
        self.assertEqual(self.client.post('/api/project', json=[]).status_code, 400)
        self.assertEqual(self.client.post('/api/project', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/api/unknown', json=self.body).status_code, 404)


if __name__ == '__main__':
    unittest.main()
