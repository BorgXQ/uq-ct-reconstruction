"""Run real local CT studies and save numerical/figure artifacts under .artifacts/."""
import base64
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from ct_editor import create_app

app=create_app();client=app.test_client()
def post(path,body):
    r=client.post(path,json=body)
    if r.status_code>=400:raise RuntimeError(r.json)
    return r.json

def job(path,body):
    submitted=post(path,body)
    while True:
        result=client.get('/api/jobs/'+submitted['job_id']).json
        if result['status'] in ('completed','canceled','failed'):
            if result['status']!='completed':raise RuntimeError(result)
            return result['result']
        time.sleep(.02)

try:
    yy,xx=np.mgrid[:512,:512];rr=((xx-245)/155)**2+((yy-260)/170)**2
    drawing=np.zeros((512,512),np.uint8);drawing[rr<1]=255;drawing[rr<.35]=64
    sid=post('/api/snapshots',dict(request_id='m5-demo-image',editor_document_id='m5-demo',editor_revision=1,source_side=512,source_encoding='grayscale_u8_base64',source_data=base64.b64encode(drawing.tobytes()).decode(),numerical_resolution=16,preprocessing_version='block_area_v1'))['snapshot_id']
    scan=job('/api/acquisitions',dict(request_id='scan',snapshot_id=sid,geometry=dict(n_angles=24,n_detectors=24,start_degrees=0,end_degrees=180),noise=dict(sigma=.02,seed=11)))
    template=job('/api/randomized-analyses',dict(request_id='template',observation_id=scan['observation_id'],source=dict(nu=.5,M=2.,provenance='Explicit experiment assumption; not fitted'),alpha=.05,
        settings_origin='declared_independent',declaration='Predeclared demo protocol',step_policy='safe_induced_norm_v1',selection=dict(mode='fixed',iterations=20,provenance='fixed_predeclared'),rank=4,oversampling=4,sketch_seed=17,probe_seed=23,probe_count=8,block_size=4,epsilon=.2,delta=.01,eta=dict(value=.1,provenance='Declared; not established'),backend='matrix_free',reference=True))['analysis_id']
    base=dict(request_id='fixed',template_id=template,protocol='fixed_algorithm',comparison='common_k',purpose='predeclared_validation',trials=100,root_seed=20261020,
        methods=['exact','tsvd','retained','corrected'],iterations=20,budget=200,pixels=[0,119],representatives=[0],diagnostics='representative',reference=True,diagnostic_iterations=[5,20,50,100])
    ids=[];reports={}
    for label,updates in [('fixed',{}),('fresh',dict(protocol='all_random_sources',comparison='own_stopping',trials=20,root_seed=20261021,diagnostic_iterations=[],methods=['retained','corrected'])),('no_crossing',dict(comparison='own_stopping',trials=3,budget=1,methods=['corrected'],diagnostic_iterations=[]))]:
        sid=job('/api/validation-studies',{**base,**updates,'request_id':label})['study_id'];ids.append(sid)
        reports[label]=client.get('/api/validation-studies/'+sid).json
    artifacts=[]
    for profile in ('numerical','figures'):
        artifacts.append(job('/api/exports',dict(request_id='export-'+profile,study_id=ids[0],profile=profile)))
    raw=client.get(artifacts[0]['download']).data
    # A fresh app has no old interactive IDs; restoration must be sufficient.
    fresh=create_app();other=fresh.test_client()
    try:
        submitted=other.post('/api/imports',data=raw,content_type='application/zip').json
        while True:
            done=other.get('/api/jobs/'+submitted['job_id']).json
            if done['status'] in ('completed','failed'):break
            time.sleep(.02)
        if done['status']!='completed':raise RuntimeError(done)
        iid=done['result']['import_id'];submitted=other.post('/api/imports/'+iid+'/replay-requests',json=dict(mode='study',request_id='replay')).json
        while True:
            done=other.get('/api/jobs/'+submitted['job_id']).json
            if done['status'] in ('completed','failed'):break
            time.sleep(.02)
        if done['status']!='completed':raise RuntimeError(done)
        reports['replay']=done['result']
    finally:fresh.extensions['ct_service'].runner.close()
    reports['artifacts']=artifacts
    path=Path('.artifacts/m5-demo.json');path.write_text(json.dumps(reports,indent=2))
    for label in ('fixed','fresh','no_crossing'):
        r=reports[label];print(label,r['completed_trials'],r['timing'])
        for method,s in r['summaries'].items():print(method,{k:s[k] for k in ('returned_intervals','mean_coverage','mean_full_width','mean_trial_rmse','outcomes','selected_k_counts')})
    print('Replay:',reports['replay']['status']);print('Evidence:',path)
finally:app.extensions['ct_service'].runner.close()
