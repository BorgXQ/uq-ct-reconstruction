"""One 32×32 common-K experiment; no parameter fitting or repeated-coverage study."""
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
        if r.status_code!=202:raise RuntimeError(r.json)
        for _ in range(24000):
            j=client.get('/api/jobs/'+r.json['job_id']).json
            if j['status']=='completed':return j['result']
            if j['status'] in ('failed','canceled'):raise RuntimeError(j)
            time.sleep(.01)
        raise RuntimeError('Timeout')
    def get(path):return client.get(path).json
    image=np.zeros((512,512),np.uint8);image[70:240,95:260]=192;image[300:405,310:425]=64;image[365:391,55:180]=137
    try:
        snap=client.post('/api/snapshots',json=dict(request_id='m4-image',editor_document_id='m4-demo',editor_revision=1,
            source_side=512,source_encoding='grayscale_u8_base64',source_data=base64.b64encode(image.tobytes()).decode(),
            numerical_resolution=32,preprocessing_version='block_area_v1')).json
        scan=job('/api/acquisitions',dict(request_id='scan',snapshot_id=snap['snapshot_id'],
            geometry=dict(n_angles=90,n_detectors=47,start_degrees=0,end_degrees=180),noise=dict(sigma=.01,seed=19)))
        exact=job('/api/exact-analyses',dict(request_id='exact',observation_id=scan['observation_id'],step_policy='safe_induced_norm_v1',
            source=dict(M=6,nu=.5,provenance='Predeclared unverified example; no tuning to truth'),bound_method='spectral',alpha=.05,
            selection=dict(mode='fixed',iterations=200,provenance='fixed_predeclared'),settings_origin='declared_independent',declaration='Fixed script settings'))
        cfg=dict(request_id='compare',exact_analysis_id=exact['analysis_id'],rank=64,oversampling=10,sketch_seed=0,probe_seed=0,
            probe_count=32,block_size=8,epsilon=.2,delta=.01,eta=dict(value=.1,provenance='Predeclared unverified example'),backend='matrix_free',reference=True)
        exact_full=get('/api/exact-analyses/'+exact['analysis_id'])
        refs=job('/api/uncertainty-comparisons',cfg);a=get('/api/randomized-analyses/'+refs['analysis_id'])
        diag=job('/api/randomized-analyses/'+a['analysis_id']+'/diagnostics',dict(request_id='diagnose'));d=get('/api/approximation-diagnostics/'+diag['diagnostic_id'])
        own=job('/api/randomized-analyses/'+a['analysis_id']+'/stopping-comparisons',dict(request_id='own',budget=1000,c0=1,bound_method='spectral'))
        comparison=get('/api/stopping-comparisons/'+own['comparison_id'])
        scalar_keys=('configuration','step','factors','probe_policy','probe_result','reference','cost','timings','bundle_seconds','elapsed_seconds','tsvd',
            'retained_bias_allowance','total_bias_allowance','full_bias_allowance','residual_reference_check','coverage_interpretation','probe_control','reference_randomized_coverage_expression')
        summary={key:a[key] for key in scalar_keys}
        for key in ('full_variance','retained_variance','signed_residual','variance','total_half_width'):
            v=np.array(a[key]);summary[key]=dict(minimum=float(v.min()),maximum=float(v.max()),mean=float(v.mean()))
        output=dict(own_stopping_timings=comparison['timings'],exact_preparation_seconds=exact_full['spectrum']['elapsed_seconds'],exact_run_seconds=exact_full['elapsed_seconds'],snapshot_hash=snap['content_hash'],operator=get('/api/acquisitions/'+scan['acquisition_id'])['operator'],comparison=summary,
            diagnostics={key:value for key,value in d.items() if not isinstance(value,list)},
            own_stopping={key:dict(status=value['status'],selected_k=value['selected_k'],last=value['history'][-1]) for key,value in comparison['selections'].items()})
        print(json.dumps(output,indent=2))
    finally:ct.runner.close()


if __name__=='__main__':main()
