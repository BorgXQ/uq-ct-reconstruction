"""Reproducible 32×32 M3 API demonstration; no parameter fitting or repeated trials."""
import base64
import json
import sys
import time
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ct_editor import create_app


def main():
    app=create_app({'TESTING':True});client=app.test_client();ct=app.extensions['ct_service']
    def job(path,body):
        r=client.post(path,json=body)
        if r.status_code!=202: raise RuntimeError(r.json)
        for _ in range(12000):
            j=client.get('/api/jobs/'+r.json['job_id']).json
            if j['status']=='completed': return j['result']
            if j['status'] in ('failed','canceled'): raise RuntimeError(j)
            time.sleep(.01)
        raise RuntimeError('Demo timeout')
    def get(path): return client.get(path).json
    image=np.zeros((512,512),np.uint8)
    image[70:240,95:260]=192;image[300:405,310:425]=64;image[365:391,55:180]=137
    try:
        snap=client.post('/api/snapshots',json=dict(request_id='demo-source',editor_document_id='m3-demo',editor_revision=1,
            source_side=512,source_encoding='grayscale_u8_base64',source_data=base64.b64encode(image.tobytes()).decode(),
            numerical_resolution=32,preprocessing_version='block_area_v1')).json
        scan=job('/api/acquisitions',dict(request_id='scan',snapshot_id=snap['snapshot_id'],
            geometry=dict(n_angles=90,n_detectors=47,start_degrees=0,end_degrees=180),noise=dict(sigma=.01,seed=19)))
        request=dict(request_id='exact',observation_id=scan['observation_id'],step_policy='exact_spectral_half',
            source=dict(M=6,nu=.5,provenance='Explicit example values, not calibrated to this image.'),bound_method='spectral',alpha=.05,
            selection=dict(mode='fixed',iterations=200,provenance='fixed_predeclared'),settings_origin='declared_independent',
            declaration='Fixed settings written into this reproducible script before generating its observation.')
        refs=job('/api/exact-analyses',request);analysis=get('/api/exact-analyses/'+refs['analysis_id'])
        refs=job('/api/exact-analyses/'+analysis['analysis_id']+'/diagnostics',dict(request_id='diagnostic'))
        diagnostic=get('/api/exact-diagnostics/'+refs['diagnostic_id'])
        auto=job('/api/exact-analyses',{**request,'request_id':'automatic','selection':dict(mode='automatic',budget=1000,c0=1)})
        automatic=get('/api/exact-analyses/'+auto['analysis_id'])
        summary={k:analysis[k] for k in ('configuration','step','spectrum','trace_covariance','max_pixel_sd','bias_allowance',
            'source_condition_status','selection_status','coverage_interpretation','elapsed_seconds')}
        for key in ('pixel_sd','noise_half_width','total_half_width','lower','upper'):
            a=np.array(analysis[key]);summary[key]=dict(minimum=float(a.min()),maximum=float(a.max()),mean=float(a.mean()))
        output=dict(snapshot_content_hash=snap['content_hash'],acquisition=get('/api/acquisitions/'+scan['acquisition_id'])['operator'],
            analysis=summary,diagnostics={k:diagnostic[k] for k in ('actual_bias_norm','rmse','relative_error','inclusion_fraction',
                'bias_bound_supported','source_check','effective_interpretation','elapsed_seconds')},
            automatic={k:automatic[k] for k in ('status','selected_k','selection','elapsed_seconds')},
            last_search_entry=get('/api/exact-analyses/'+auto['analysis_id']+'/stopping-history')['history'][-1])
        print(json.dumps(output,indent=2))
    finally: ct.runner.close()


if __name__=='__main__': main()
