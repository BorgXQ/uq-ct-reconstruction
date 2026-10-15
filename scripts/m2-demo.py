"""Reproducible M2 measurement via the real snapshot/job API; writes JSON to stdout."""
import base64
import json
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ct_editor import create_app


def main():
    app = create_app({'TESTING': True})
    client = app.test_client()
    service = app.extensions['ct_service']

    def submit(path, body):
        response = client.post(path, json=body)
        if response.status_code != 202:
            raise RuntimeError(response.json)
        job_id = response.json['job_id']
        for _ in range(6000):
            job = client.get('/api/jobs/' + job_id).json
            if job['status'] == 'completed':
                return job['result']
            if job['status'] in ('failed', 'canceled'):
                raise RuntimeError(job)
            time.sleep(.01)
        raise RuntimeError('Demo job timeout')

    # Asymmetric, different gray regions, deliberately unaligned with target blocks.
    source = np.zeros((512, 512), np.uint8)
    source[70:240, 95:260] = 192
    source[300:405, 310:425] = 64
    source[365:391, 55:180] = 137
    try:
        snapshot = client.post('/api/snapshots', json=dict(request_id='demo-source',
            editor_document_id='m2-demo', editor_revision=1, source_side=512,
            source_encoding='grayscale_u8_base64', source_data=base64.b64encode(source.tobytes()).decode(),
            numerical_resolution=32, preprocessing_version='block_area_v1'))
        if snapshot.status_code != 201:
            raise RuntimeError(snapshot.json)
        sid = snapshot.json['snapshot_id']
        results = []
        for angles, sigma, K in [(90, 0, 200), (90, .01, 200), (2, 0, 5000)]:
            refs = submit('/api/acquisitions', dict(request_id='demo-scan', snapshot_id=sid,
                geometry=dict(n_angles=angles, n_detectors=47, start_degrees=0, end_degrees=180),
                noise=dict(sigma=sigma, seed=19)))
            acquisition = client.get('/api/acquisitions/' + refs['acquisition_id']).json
            refs = submit('/api/reconstructions', dict(request_id='demo-run', observation_id=refs['observation_id'],
                iterations=K, step={'mode': 'automatic_safe'}))
            rid = refs['reconstruction_id']
            record = client.get('/api/reconstructions/' + rid).json
            history = client.get('/api/reconstructions/' + rid + '/history').json
            results.append(dict(angles=angles, detectors=47, sigma=sigma, seed=19,
                operator=acquisition['operator'], cache_hit=acquisition['operator_cache_hit'],
                acquisition_seconds=acquisition['elapsed_seconds'], projection_seconds=acquisition['projection_seconds'],
                configuration=record['configuration'], completed_iterations=record['completed_iterations'],
                reconstruction_seconds=record['elapsed_seconds'], metrics=history['history'][-1],
                validation=history['diagnostics']['history'][-1]))
        print(json.dumps(dict(snapshot_content_hash=snapshot.json['content_hash'], results=results), indent=2))
    finally:
        service.runner.close()


if __name__ == '__main__':
    main()
