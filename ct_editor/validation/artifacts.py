"""Bounded, non-executable ZIP/JSON/NPY bundles and explicit numerical replay."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from uuid import uuid4
import importlib.metadata
import json
import math
import os
import platform
import stat
import zipfile
from time import perf_counter
import numpy as np
from scipy.sparse import csr_matrix
from ..images import InputError
from ..ct.geometry import Geometry,Operator,integer
from ..ct.jobs import now
from ..randomized.numerics import factor_record
from .service import plain,validate_plan,size
from .engine import SEED_VERSION

SCHEMA='ct_validation_bundle_v1'
MAX_MEMBERS=4096
MAX_ARRAY=128*1024**2


def environment():
    return dict(python=platform.python_version(),numpy=np.__version__,scipy=importlib.metadata.version('scipy'),
        matplotlib=importlib.metadata.version('matplotlib'),precision='float64',generator='PCG64',seed_version=SEED_VERSION)


def provenance():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    files=list((root/'ct_editor').rglob('*.py'))
    h=sha256()
    for file in sorted(files):h.update(str(file.relative_to(root)).encode());h.update(file.read_bytes())
    docs={}
    for file in [root/'docs'/'prod.md',root/'docs'/'implementation.md',*sorted((root/'docs'/'milestones').glob('*.md'))]:
        docs[str(file.relative_to(root))]=sha256(file.read_bytes()).hexdigest()
    return dict(code_fingerprint=h.hexdigest(),source_documents=docs,revision_status='Code fingerprint recorded; no claim of a clean Git revision')


def encode_tree(value,members):
    if isinstance(value,np.ndarray):
        name=f'arrays/{len(members):05d}.npy';buf=BytesIO();np.save(buf,value,allow_pickle=False);members[name]=buf.getvalue()
        return {'__array__':name}
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,dict):return {str(k):encode_tree(v,members) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [encode_tree(v,members) for v in value]
    return value


def strict_json(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError('Duplicate JSON key')
            d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda s:(_ for _ in ()).throw(ValueError('Nonfinite JSON')),parse_float=lambda s:float(s) if math.isfinite(float(s)) else (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def load_array(raw):
    buf=BytesIO(raw);version=np.lib.format.read_magic(buf)
    if version==(1,0):shape,fortran,dtype=np.lib.format.read_array_header_1_0(buf)
    elif version==(2,0):shape,fortran,dtype=np.lib.format.read_array_header_2_0(buf)
    else:raise ValueError('Unsupported NPY version')
    if dtype.hasobject or dtype.kind not in 'biuf' or len(shape)>2 or any(type(s) is not int or s<0 for s in shape):raise ValueError('Unsupported numeric array')
    count=math.prod(shape);length=count*dtype.itemsize
    if length>MAX_ARRAY or length!=len(raw)-buf.tell():raise ValueError('Array size/header mismatch')
    if fortran:raise ValueError('Only C-order arrays supported')
    a=np.load(BytesIO(raw),allow_pickle=False)
    if not np.isfinite(a).all():raise ValueError('Nonfinite array')
    return a


def decode_tree(value,members,depth=0,cache=None):
    if cache is None:cache={}
    if depth>40:raise ValueError('Metadata nesting limit')
    if isinstance(value,dict):
        if '__array__' in value:
            if set(value)!= {'__array__'} or value['__array__'] not in members:raise ValueError('Invalid array reference')
            name=value['__array__']
            if name not in cache:cache[name]=load_array(members[name])
            return cache[name]
        return {k:decode_tree(v,members,depth+1,cache) for k,v in value.items()}
    if isinstance(value,list):return [decode_tree(v,members,depth+1,cache) for v in value]
    if isinstance(value,str) and len(value)>20000:raise ValueError('Metadata string too long')
    return value


def read_bundle(raw,limit):
    if len(raw)>limit:raise ValueError('Archive exceeds compressed limit')
    with zipfile.ZipFile(BytesIO(raw)) as z:
        infos=z.infolist()
        if len(infos)>MAX_MEMBERS:raise ValueError('Archive member limit')
        total=0;seen=set();members={}
        for info in infos:
            name=info.filename;p=PurePosixPath(name);mode=info.external_attr>>16
            if (not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts or '.' in p.parts or
                str(p)!=name or stat.S_ISLNK(mode) or info.is_dir() or name.casefold() in seen):raise ValueError('Unsafe archive member')
            seen.add(name.casefold());total+=info.file_size
            if total>limit or info.file_size>MAX_ARRAY+65536 or info.file_size>max(1024**2,info.compress_size*300):raise ValueError('Expanded archive limit')
            if info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):raise ValueError('Unsupported compression')
        for info in infos:members[info.filename]=z.read(info)
    if 'manifest.json' not in members:raise ValueError('Missing manifest')
    manifest=strict_json(members.pop('manifest.json'))
    if manifest.get('schema')!=SCHEMA or manifest.get('profile') not in ('numerical','single_result','parameter_study'):raise ValueError('Unsupported bundle schema/profile')
    inventory=manifest['inventory']
    if set(members)!=set(inventory):raise ValueError('Inventory mismatch')
    for name,b in members.items():
        if inventory[name]!={'bytes':len(b),'sha256':sha256(b).hexdigest()}:raise ValueError('Payload integrity mismatch')
        if name not in ('study.json','summary.md') and not (name.startswith('arrays/') and name.endswith('.npy')):raise ValueError('Unexpected numerical payload')
    result=decode_tree(strict_json(members['study.json']),members)
    if manifest['profile']=='parameter_study':
        if not isinstance(result,dict) or not isinstance(result.get('studies'),list) or len(result['studies'])>16:raise ValueError('Invalid grid bundle')
        for study in result['studies']:validate_result(study)
        ids={study['study_id'] for study in result['studies']}
        if len(ids)!=len(result['studies']):raise ValueError('Duplicate grid study')
        for row in result['grid']['rows']:
            if row.get('study_id') and row['study_id'] not in ids:raise ValueError('Missing grid child')
    else:validate_result(result)
    return manifest,result


def validate_result(r):
    """Validate relationships, not imported assertions of theoretical validity."""
    p=r['plan'];c=r['configuration'];g=p['geometry'];n=g['resolution']**2
    if g['resolution'] not in (16,32,64):raise ValueError('Unsupported image grid')
    spec=Geometry.arrays(g['resolution'],g['angles'],g['offsets'],g['range_degrees'])
    if spec.geometry_hash!=g['geometry_hash']:raise ValueError('Geometry version/hash mismatch')
    if p.get('schema')!='validation_plan_v1' or p['seed_version']!=SEED_VERSION:raise ValueError('Unsupported plan/seed version')
    validate_plan(p,c,n)
    if c.get('algorithm','gaussian_normal_projected_v1')!='gaussian_normal_projected_v1':raise ValueError('Unsupported randomized algorithm')
    if c.get('numerical_policy','full_svd_stable_filters_v1')!='full_svd_stable_filters_v1':raise ValueError('Unsupported numerical policy')
    inputs=r['inputs'];m=spec.shape[0]
    for key,length in [('truth',n),('clean',m)]:
        if not isinstance(inputs[key],np.ndarray) or inputs[key].shape!=(length,) or inputs[key].dtype!=np.dtype('float64'):raise ValueError('Input dimension/dtype mismatch')
    if np.min(inputs['truth'])<0 or np.max(inputs['truth'])>1:raise ValueError('Drawing outside density range')
    d,ix,ptr=inputs['csr_data'],inputs['csr_indices'],inputs['csr_indptr']
    if list(spec.shape)!=inputs['csr_shape'] or ptr.shape!=(m+1,) or d.ndim!=1 or ix.shape!=d.shape or ix.dtype.kind not in 'iu' or ptr.dtype.kind not in 'iu':raise ValueError('CSR dimensions/types')
    if ptr[0]!=0 or ptr[-1]!=len(d) or np.any(np.diff(ptr)<0) or np.any(ix<0) or np.any(ix>=n):raise ValueError('Invalid CSR indices')
    op=Operator(spec,csr_matrix((d,ix,ptr),shape=spec.shape))
    if op.metadata()['csr_checksum']!=r['provenance']['operator']['csr_checksum']:raise ValueError('Operator checksum mismatch')
    if not np.allclose(op.forward(inputs['truth']),inputs['clean'],rtol=1e-12,atol=1e-12):raise ValueError('Clean projection mismatch')
    R=p['trials'];seen=set();counts={method:dict(terminal=0,centers=0,intervals=0) for method in p['methods']}
    if not isinstance(r['ledger'],list) or len(r['ledger'])>R*len(p['methods']):raise ValueError('Ledger length')
    for row in r['ledger']:
        integer(row['trial_index'],'trial',0,R-1);key=(row['trial_index'],row['method'])
        if row['method'] not in counts or key in seen:raise ValueError('Duplicate/incompatible trial')
        seen.add(key);counter=counts[row['method']];counter['terminal']+=1
        if row['status'] in ('completed_interval','completed_moments_only'):
            integer(row['k'],'k',1,5000);counter['centers']+=1
        if row['status']=='completed_interval':counter['intervals']+=1
    for method in p['methods']:
        s=r['summaries'][method];a=r['maps'][method];ct=counts[method]
        if s['planned']!=R or s['terminal']!=ct['terminal'] or s['numeric_centers']!=ct['centers'] or s['returned_intervals']!=ct['intervals'] or s['not_started']!=R-ct['terminal']:raise ValueError('Count inconsistency')
        for value in a.values():
            if value is not None and (not isinstance(value,np.ndarray) or value.shape!=(n,)):raise ValueError('Aggregate shape')
        inc=a['inclusion_count']
        if inc.dtype.kind not in 'iu' or np.any(inc<0) or np.any(inc>ct['intervals']):raise ValueError('Invalid inclusion counters')
        if ct['intervals']:
            if not np.allclose(a['coverage'],inc/ct['intervals'],rtol=0,atol=1e-15):raise ValueError('Coverage mismatch')
        elif a['coverage'] is not None:raise ValueError('Coverage without intervals')
    if set(r['series'])!=set(p['methods']):raise ValueError('Series method mismatch')
    for method,series in r['series'].items():
        if len(series)>16:raise ValueError('Series retention limit')
        for pixel,rows in series.items():
            if not pixel.isdecimal() or str(int(pixel))!=pixel or int(pixel) not in p['pixels']:raise ValueError('Series pixel identity')
            if not isinstance(rows,list) or len(rows)>R:raise ValueError('Series count')
            seen_trials=set()
            for row in rows:
                integer(row['trial_index'],'series trial',0,R-1)
                if row['trial_index'] in seen_trials:raise ValueError('Duplicate series trial')
                seen_trials.add(row['trial_index'])
                for key in ('center','lower','upper'):
                    if row[key] is not None and (isinstance(row[key],bool) or not isinstance(row[key],(int,float)) or not math.isfinite(row[key])):raise ValueError('Invalid series scalar')
    if len(r['representatives'])>8*len(p['methods']):raise ValueError('Representative count')
    for key,t in r['representatives'].items():
        if key!=str(t['trial_index'])+'_'+t['method']:raise ValueError('Representative key mismatch')
        if t['method'] not in p['methods'] or t['trial_index'] not in p['representatives']:raise ValueError('Representative identity mismatch')
        if t['center'].shape!=(n,) or t['y'].shape!=(m,):raise ValueError('Representative shape')
        for field in ('center','variance','noise_half_width','half_width','lower','upper'):
            if t[field] is not None and (not isinstance(t[field],np.ndarray) or t[field].shape!=(n,)):raise ValueError('Representative array shape')
        if t['half_width'] is not None:
            if t['bias_allowance'] is None or not np.allclose(t['half_width'],t['noise_half_width']+t['bias_allowance'],rtol=1e-10,atol=1e-12):raise ValueError('Interval allowance identity mismatch')
            if np.any(t['half_width']<0) or not np.allclose(t['lower'],t['center']-t['half_width'],rtol=0,atol=1e-12) or not np.allclose(t['upper'],t['center']+t['half_width'],rtol=0,atol=1e-12):raise ValueError('Endpoint/component mismatch')
    f=r['fixed_factors']
    if f:
        rank=c['rank']
        if f['values'].shape!=(rank,) or f['vectors'].shape!=(n,rank) or np.any(f['values']<=0) or not np.allclose(f['vectors'].T@f['vectors'],np.eye(rank),atol=1e-9):raise ValueError('Invalid factor payload')


def summary_text(r):
    p=r['plan'];lines=['# CT validation experiment',f"Status: {r['status']}; protocol: {p['protocol']}; comparison: {p['comparison']}.",
        f"Grid {p['geometry']['resolution']}²; projections {len(p['geometry']['angles'])}; detectors {len(p['geometry']['offsets'])}; sigma {r['configuration']['sigma']}; omega {r['step']['omega']}.",
        f"Completed trials {r['completed_trials']} / {p['trials']}; root {p['root_seed']}; purpose {p['purpose']}.",r['conditioning'],r['interpretation']]
    for method,s in r['summaries'].items():lines.append(f"{method}: returned intervals {s['returned_intervals']}/{s['planned']}; spatial mean marginal coverage {s['mean_coverage']}; mean full width {s['mean_full_width']}; mean trial RMSE {s['mean_trial_rmse']}; outcomes {s['outcomes']}.")
    lines+=['Coverage uses returned intervals; returned-and-covered uses terminal attempts. Pixels are correlated. Wilson intervals quantify Monte Carlo error, not image intensity uncertainty.',
        'Failed assumptions remain in the numerical sample. Replays reproduce existing draws and are not independent evidence.',
        'Stage times (seconds): '+json.dumps(r['timing']), 'Source diagnostic: '+json.dumps(r['source_check']),
        'Numerical payload: study.json with NPY references; manifest.json records exact inventory and hashes.']
    return '\n\n'.join(lines)+'\n'


class Artifacts:
    def __init__(self,service):self.service=service;self.exports={};self.imports={}

    def export(self,sid,profile,request_id,trial=0):
        if profile not in ('numerical','single_result','parameter_study','figures'):raise InputError('Unsupported export profile.')
        if profile=='parameter_study':
            if sid not in self.service.grids or self.service.grids[sid]['status'] not in ('completed','partial_canceled'):raise InputError('Select a terminal parameter study.')
            grid=deepcopy(self.service.grids[sid]);r=dict(grid=grid,studies=[self.service.get(row['study_id']) for row in grid['rows'] if row.get('study_id')])
        else:
            r=self.service.get(sid)
            if profile=='single_result' and not any(v['trial_index']==trial for v in r['representatives'].values()):raise InputError('Selected trial not retained. Replay it explicitly before exporting.')
        aid=uuid4().hex;root=self.service.directory;limit=self.service.disk_limit
        lease=self.service.ct.budget.reserve(size(r)*3+32*1024**2)
        def work(job):
            start=perf_counter();job.progress('Preparing export',0,None);members={}
            if profile in ('numerical','single_result','parameter_study'):
                tree=encode_tree(r,members);members['study.json']=json.dumps(tree,allow_nan=False,separators=(',',':')).encode()
            else:
                from .figures import figures
                members.update(figures(r,job))
            members['summary.md']=('\n\n'.join(summary_text(v) for v in r['studies']) if profile=='parameter_study' else summary_text(r)).encode();job.check()
            manifest=dict(schema=SCHEMA,profile=profile,created_at=now(),environment=environment(),provenance=provenance(),
                interpretation='Imported/empirical results do not establish probability conditions',selected_trial=trial if profile=='single_result' else None,replay_tolerance=dict(rtol=1e-9,atol=1e-11),
                regeneration='Representative raw observations retained. Other draws use the versioned schedule. No original editor raster retained.',
                inventory={k:dict(bytes=len(b),sha256=sha256(b).hexdigest()) for k,b in members.items()})
            members['manifest.json']=json.dumps(manifest,allow_nan=False).encode()
            total=sum(map(len,members.values()));used=sum(f.stat().st_size for f in root.iterdir() if f.is_file())
            if total+used>limit:raise InputError('Artifact disk capacity exceeded; prior bundles preserved.',code='storage_limit',status=507)
            path=root/(aid+'.zip');tmp=root/(aid+'.tmp')
            try:
                with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_STORED) as z:
                    for i,(name,b) in enumerate(members.items()):job.check();z.writestr(name,b);job.progress('Writing artifact',i+1,len(members))
                job.check()
                def publish():
                    os.replace(tmp,path);self.exports[aid]=dict(export_id=aid,profile=profile,status='ready',study_id=sid,bytes=path.stat().st_size,
                        sha256=sha256(path.read_bytes()).hexdigest(),seconds=perf_counter()-start,download=f'/api/exports/{aid}/download')
                    return self.exports[aid]
                job.commit(publish)
            finally:
                if tmp.exists():tmp.unlink()
        try:return self.service.ct.runner.submit('export',request_id,dict(study_id=sid,profile=profile),work,lease.close)
        except Exception:lease.close();raise

    def restore(self,raw,request_id):
        limit=self.service.disk_limit
        if len(raw)>limit:raise InputError('Import too large.',code='body_too_large',status=413)
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                infos=archive.infolist()
                if len(infos)>MAX_MEMBERS:raise ValueError('Archive member limit')
                expanded=sum(info.file_size for info in infos)
                if expanded>min(limit,self.service.ct.budget.limit//2):raise ValueError('Expanded archive limit')
        except (ValueError,zipfile.BadZipFile) as exc:raise InputError(str(exc),code='invalid_bundle',status=422) from exc
        lease=self.service.ct.budget.reserve(len(raw)*2+expanded*4+8*1024**2);iid=uuid4().hex
        def work(job):
            job.progress('Validating numerical bundle',0,None)
            try:manifest,r=read_bundle(raw,min(limit,self.service.ct.budget.limit//2))
            except (ValueError,KeyError,TypeError,OverflowError,zipfile.BadZipFile,InputError,RecursionError) as exc:raise InputError('Invalid or incompatible numerical bundle: '+str(exc),code='invalid_bundle',status=422) from exc
            studies=r['studies'] if manifest['profile']=='parameter_study' else [r]
            if len(self.service.records)+len(studies)>self.service.limit:raise InputError('Import exceeds study capacity.',code='storage_limit',status=507)
            compatible=manifest['environment']==environment() and manifest.get('provenance',{}).get('code_fingerprint')==provenance()['code_fingerprint'];mapping={}
            for study in studies:
                original=study['study_id'];sid=uuid4().hex;mapping[original]=sid;study['study_id']=sid;study['imported']=True
                study['original_interpretation']=study['interpretation'];study['interpretation']='Imported numerical results; source/bias and probability claims are unverified. Restoration checks integrity, not theorem applicability.'
                study['import_status']=dict(original_study_id=original,integrity='checked',claims='Imported claims; no mathematical certificate established',environment_compatible=compatible)
            job.check();retained=self.service.ct.budget.reserve(sum(size(study) for study in studies))
            try:
                def publish():
                    with self.service.lock:
                        self.service.leases.append(retained)
                        for study in studies:self.service.records[study['study_id']]=study
                        grid_id=None
                        if manifest['profile']=='parameter_study':
                            grid=deepcopy(r['grid']);grid_id=uuid4().hex;grid['grid_id']=grid_id
                            for row in grid['rows']:
                                if row.get('study_id'):row['study_id']=mapping[row['study_id']]
                            self.service.grids[grid_id]=grid
                        self.imports[iid]=dict(import_id=iid,study_id=studies[0]['study_id'] if studies else None,study_ids=list(mapping.values()),grid_id=grid_id,id_mapping=mapping,status='restored',environment_compatible=compatible,claims='Imported claims only',manifest=manifest)
                    return self.imports[iid]
                job.commit(publish)
            except Exception:retained.close();raise
        try:return self.service.ct.runner.submit('import',request_id,dict(import_id=iid),work,lease.close)
        except Exception:lease.close();raise

    def replay(self,sid,data):
        r=self.service.get(sid);mode=data.get('mode','trial')
        if mode not in ('trial','study','pixel'):raise InputError('Choose trial, study or pixel replay.')
        p=deepcopy(r['plan']);c=deepcopy(r['configuration']);g=p['geometry'];spec=Geometry.arrays(g['resolution'],g['angles'],g['offsets'],g['range_degrees'])
        estimate=self.service.estimate(p,c,spec);trial=data.get('trial',0);pixel=data.get('pixel',0)
        integer(trial,'trial',0,p['trials']-1);integer(pixel,'pixel',0,spec.shape[1]-1)
        if r.get('import_status',{}).get('environment_compatible') is False:raise InputError('Stored environment differs; inspection is available but supported replay is unavailable.',code='incompatible_environment',status=422)
        if mode=='pixel':p['pixels']=[pixel]
        if mode=='trial':p['representatives']=sorted(set(p['representatives']+[trial]))
        rid=uuid4().hex;lease=self.service.ct.budget.reserve(estimate['workspace_bytes'])
        def work(job):
            inputs=r['inputs'];op=Operator(spec,csr_matrix((inputs['csr_data'],inputs['csr_indices'],inputs['csr_indptr']),shape=spec.shape))
            factors=None;ff=r['fixed_factors']
            if ff:factors=factor_record(ff['values'],ff['vectors'],{k:v for k,v in ff['metadata'].items() if k not in ('n','effective_rank','eigenvalue_underflow_modes','factor_id')})
            observations={v['trial_index']:v['y'] for v in r['representatives'].values()}
            rr=self.service.run(uuid4().hex,p,c,spec,inputs['truth'],r['step'],job,op,factors,observations,[trial] if mode=='trial' else None)
            checks=[]
            def compare(name,a,b):
                if a is None or b is None:checks.append(dict(quantity=name,agrees=a is None and b is None));return
                a=np.asarray(a);b=np.asarray(b);ok=a.shape==b.shape and np.allclose(a,b,rtol=1e-9,atol=1e-11)
                checks.append(dict(quantity=name,agrees=bool(ok),maximum_absolute_difference=float(np.max(np.abs(a-b))) if a.shape==b.shape and a.size else None))
            old={(v['trial_index'],v['method']):v for v in r['ledger']}
            for row in rr['ledger']:
                previous=old.get((row['trial_index'],row['method']))
                if previous:
                    checks.append(dict(quantity=f"trial {row['trial_index']} {row['method']} status/K",agrees=(row['status'],row['k'])==(previous['status'],previous['k'])))
                    for key in ('rmse','mean_width','spatial_inclusion'):compare(f"trial {row['trial_index']} {row['method']}/{key}",row.get(key),previous.get(key))
            for key,value in rr['representatives'].items():
                if key in r['representatives']:
                    for field in ('y','center','half_width','lower','upper'):compare(key+'/'+field,value[field],r['representatives'][key][field])
            if mode=='study' and r['status']=='completed':
                for m in p['methods']:
                    for key,value in r['maps'][m].items():compare(m+'/'+key,rr['maps'][m].get(key),value)
                    checks.append(dict(quantity=m+'/counts',agrees=rr['summaries'][m]['outcomes']==r['summaries'][m]['outcomes']))
            report=dict(replay_id=rid,original_study_id=sid,mode=mode,checks=checks,
                status='mismatch' if any(not q['agrees'] for q in checks) else 'agreement' if checks else 'regenerated_no_retained_reference',
                note='Recorded schedule replay; not new independent trials. Partial originals rerun as a new result; no merging or duplicated counts.',
                replay_study_id=rr['study_id'],timing=rr['timing'])
            job.check()
            def publish():self.service.publish(rr['study_id'],rr);self.service.replays[rid]=report;return report
            job.commit(publish)
        try:return self.service.ct.runner.submit('replay',data.get('request_id',rid),dict(study_id=sid,mode=mode),work,lease.close)
        except Exception:lease.close();raise
