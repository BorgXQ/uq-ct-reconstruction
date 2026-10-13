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

    def test_axis_aligned_line_lengths_and_orientation(self):
        n = 64
        a, _, offsets, _ = build_operator(n, 2)
        image = np.ones((n, n))
        sino = (a @ image.ravel()).reshape(2, -1)
        expected = np.where(np.abs(offsets) <= 1, 2., 0.)
        np.testing.assert_allclose(sino, np.tile(expected, (2, 1)), atol=1e-12)
        image[:] = 0
        image[4:9, 41:46] = .6
        sino = (a @ image.ravel()).reshape(2, -1)
        self.assertGreater(offsets @ sino[0], 0)  # right of center
        self.assertLess(offsets @ sino[1], 0)  # above center

    def test_diagonal_ray_length(self):
        a, _, offsets, _ = build_operator(64, 4)
        sino = (a @ np.ones(64**2)).reshape(4, -1)
        expected = np.maximum(0, 2 * (np.sqrt(2) - np.abs(offsets)))
        np.testing.assert_allclose(sino[1], expected, atol=1e-12)

    def test_density_preserved(self):
        image = normalize_image(np.full((64, 64), 127 / 255))
        self.assertEqual(image[0, 0], 127 / 255)
        for invalid in [np.zeros((12, 12)), np.full((64, 64), np.nan), np.full((64, 64), 1.1)]:
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
        small, _, _, omega = build_operator(64, 2)
        largest_squared = np.linalg.norm(small.toarray(), 2)**2
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
        for n, expected in [(128, 64), (256, 16)]:
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
        self.body = {'image':np.full((64, 64), .4).tolist(), 'angles':12, 'iterations':4}

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

    def test_large_request_and_removed_grid(self):
        # Long decimals exceed the previous 512 KiB request limit.
        body = self.body | {'image':np.full((256,256), 127/255).tolist(), 'angles':2, 'iterations':1}
        for action in ['project', 'reconstruct']:
            response = self.client.post('/api/' + action, json=body)
            self.assertEqual(response.status_code, 200)

    def test_bad_inputs(self):
        for change in [{'noise_std':-1}, {'noise_std':1.1}, {'noise_std':True}, {'noise_std':'0.1'}, {'noise_std':float('nan')}, {'noise_seed':-1}, {'noise_seed':1.5}, {'noise_seed':True}, {'image':np.zeros((32,32)).tolist()}, {'angles':0}, {'angles':181}, {'angles':2.5}, {'angles':True}, {'iterations':1001}, {'image':[]}]:
            self.assertEqual(self.client.post('/api/reconstruct', json=self.body | change).status_code, 400)
        self.assertEqual(self.client.post('/api/project', json=[]).status_code, 400)
        self.assertEqual(self.client.post('/api/project', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/api/unknown', json=self.body).status_code, 404)


if __name__ == '__main__':
    unittest.main()
