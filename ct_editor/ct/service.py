"""Simulation orchestration, immutable records, operator cache and replay."""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import secrets
from threading import RLock
from time import perf_counter
from uuid import uuid4

import numpy as np
from ..images import InputError, canonical
from .geometry import Geometry, build_operator, fields, identifier, integer, scalar
from .solver import frozen_array, landweber, replay, resolve_step
from .diagnostics import ImageDiagnostics
from .jobs import JobRunner, now


def digest(header,*arrays):
    return hashlib.sha256(canonical(header,b''.join(np.asarray(a,dtype='<f8').tobytes() for a in arrays))).hexdigest()


@dataclass(frozen=True)
class Record:
    header: str
    payloads: tuple  # (name, immutable little-endian bytes)
    @classmethod
    def make(cls,metadata,**arrays):
        return cls(json.dumps(metadata,sort_keys=True,allow_nan=False),tuple((key,frozen_array(value).tobytes()) for key,value in arrays.items()))
    def meta(self): return json.loads(self.header)
    def array(self,name):
        for key,data in self.payloads:
            if key==name: return np.frombuffer(data,dtype='<f8')
        raise KeyError(name)
    def response(self):
        return {**self.meta(),**{key:self.array(key).tolist() for key,_ in self.payloads}}
    def size(self): return len(self.header.encode())+sum(len(value) for _,value in self.payloads)


def noise_settings(data):
    fields(data,('sigma','seed','new_seed'),('sigma',))
    sigma=scalar(data['sigma'],'sigma')
    if data.get('new_seed') is True:
        if 'seed' in data: raise InputError('Choose seed or new_seed, not both.','seed')
        seed=secrets.randbits(32)
    else:
        if 'new_seed' in data: raise InputError('new_seed must be true or omitted.','new_seed')
        seed=integer(data.get('seed'),'seed',0,2**32-1)
    return dict(sigma=sigma,seed=seed,generator='PCG64',stream_role='measurement_noise',stream_key=[0],policy='seedsequence_spawn_key_v1')


def sample_observation(clean,settings):
    rng=np.random.Generator(np.random.PCG64(np.random.SeedSequence(settings['seed'],spawn_key=(0,))))
    with np.errstate(over='ignore',invalid='ignore'):
        noise=np.zeros_like(clean) if settings['sigma']==0 else settings['sigma']*rng.standard_normal(len(clean))
        y=clean+noise
    return frozen_array(y),frozen_array(noise)


class CTService:
    def __init__(self,snapshots,budget,config):
        self.snapshots=snapshots;self.budget=budget;self.lock=RLock()
        self.runner=JobRunner(config.get('CT_PENDING_LIMIT',4),config.get('CT_JOB_LIMIT',128))
        self.limits={'acquisitions':config.get('CT_ACQUISITION_LIMIT',32),'observations':config.get('CT_OBSERVATION_LIMIT',64),'reconstructions':config.get('CT_RECONSTRUCTION_LIMIT',32)}
        self.records={key:{} for key in self.limits};self.pending={key:0 for key in self.limits};self.retained_leases=[]
        self.specs={};self.cache=OrderedDict();self.cache_bytes=0;self.cache_limit=config.get('CT_OPERATOR_CACHE_BYTES',128*1024**2)
        self.inspections=OrderedDict();self.replay_bytes=0;self.replay_limit=config.get('CT_REPLAY_BYTES',8*1024**2)
    def _get(self,kind,key):
        with self.lock: record=self.records[kind].get(key)
        if record is None: raise InputError(f'{kind[:-1].capitalize()} unavailable in this server process.',code='not_found',status=404)
        return record
    def list(self,kind):
        with self.lock:
            output=[]
            for record in self.records[kind].values():
                meta=record.meta();meta.pop('history',None);meta.pop('diagnostics',None)
                # Coordinate arrays belong to individual retrieval, not list/status polling.
                if 'geometry' in meta:
                    meta['geometry']={k:v for k,v in meta['geometry'].items() if k not in ('angles','offsets')}
                output.append(meta)
            return output
    def _submit(self,kind,request_id,inputs,slots,workspace,work):
        with self.lock:
            for table in slots:
                if len(self.records[table])+self.pending[table]>=self.limits[table]:
                    raise InputError(f'{table.capitalize()} capacity reached; existing records are preserved.',code='storage_limit',status=507)
            lease=self.budget.reserve(workspace)
            for table in slots: self.pending[table]+=1
        def cleanup():
            with self.lock:
                for table in slots: self.pending[table]-=1
                lease.close()
        try: return self.runner.submit(kind,request_id,inputs,work,cleanup)
        except Exception: cleanup();raise
    def _publish(self,items):
        # Reserve retained bytes before any record is visible. All-or-nothing insertion.
        lease=self.budget.reserve(sum(record.size() for _,_,record in items))
        with self.lock:
            self.retained_leases.append(lease)
            for table,key,record in items: self.records[table][key]=record
    def operator(self,spec,job):
        with self.lock:
            if spec.geometry_hash in self.cache:
                op,_=self.cache.pop(spec.geometry_hash);self.cache[spec.geometry_hash]=(op,_);return op,True
        op=build_operator(spec,job.check,job.progress);job.check();size=op.metadata()['storage_bytes']
        with self.lock:
            while self.cache and self.cache_bytes+size>self.cache_limit:
                _,(old,lease)=self.cache.popitem(last=False);self.cache_bytes-=old.metadata()['storage_bytes'];lease.close()
            if size<=self.cache_limit:
                lease=self.budget.reserve(size)
                self.cache[spec.geometry_hash]=(op,lease);self.cache_bytes+=size
        return op,False
    def _observation(self,acquisition,clean,settings):
        y,noise=sample_observation(clean,settings);oid=uuid4().hex
        meta=dict(observation_id=oid,acquisition_id=acquisition['acquisition_id'],operator_id=acquisition['operator_id'],
            geometry_hash=acquisition['operator_id'],resolution=acquisition['resolution'],shape=acquisition['shape'],flatten_order='angle_major',
            dtype='float64',created_at=now(),noise=settings)
        meta['content_hash']=digest({'operator_id':meta['operator_id'],'noise':settings,'shape':meta['shape']},y)
        # Realized noise is derivable for simulation inspection, never passed to solver.
        return Record.make(meta,y=y),noise
    def acquire(self,data):
        fields(data,('request_id','snapshot_id','geometry','noise'),('request_id','snapshot_id','geometry','noise'))
        rid=identifier(data['request_id'],'request_id');sid=identifier(data['snapshot_id'],'snapshot_id')
        snapshot=self.snapshots.get(sid);truth=self.snapshots.array(sid).ravel();spec=Geometry.request(snapshot['resolution'],data['geometry']);noise=noise_settings(data['noise'])
        aid=uuid4().hex;inputs=dict(snapshot_id=sid,geometry_hash=spec.geometry_hash,noise=noise)
        def work(job):
            started=perf_counter();op,hit=self.operator(spec,job);job.check()
            if op.metadata()['L_safe']<=0: raise InputError('All rays miss the image. Increase detector count or change angles.',code='degenerate_operator',status=422)
            job.progress('Computing clean measurements',0,1)
            projection_start=perf_counter();clean=None
            with self.lock:
                for previous in self.records['acquisitions'].values():
                    m=previous.meta()
                    if m['snapshot_content_hash']==snapshot['content_hash'] and m['operator_id']==spec.geometry_hash:
                        clean=previous.array('y_clean');break
            clean_hit=clean is not None
            if clean is None: clean=frozen_array(op.forward(truth))
            job.check();projection_seconds=perf_counter()-projection_start
            meta=dict(acquisition_id=aid,created_at=now(),snapshot_id=sid,snapshot_content_hash=snapshot['content_hash'],snapshot_source_hash=snapshot['source_hash'],
                resolution=spec.resolution,shape=[len(spec.angles),len(spec.offsets)],dtype='float64',flatten_order='angle_major',
                image_grid={k:snapshot[k] for k in ('domain','pixel_width','row_origin','column_origin','flatten_order','preprocessing')},
                operator_id=spec.geometry_hash,geometry=spec.metadata(),operator=op.metadata(),operator_cache_hit=hit,clean_cache_hit=clean_hit,
                projection_seconds=projection_seconds)
            meta['clean_hash']=digest({'operator_id':spec.geometry_hash,'shape':meta['shape']},clean)
            job.progress('Sampling observation',0,1);observation,_=self._observation(meta,clean,noise);job.check()
            meta['elapsed_seconds']=perf_counter()-started
            acquisition=Record.make(meta,y_clean=clean)
            def publish():
                self._publish([('acquisitions',aid,acquisition),('observations',observation.meta()['observation_id'],observation)])
                with self.lock: self.specs[spec.geometry_hash]=spec
                return dict(acquisition_id=aid,observation_id=observation.meta()['observation_id'])
            job.progress('Scan ready',1,1);job.commit(publish)
        m,n=spec.shape
        return self._submit('acquisition',rid,inputs,['acquisitions','observations'],spec.estimate_bytes()+m*80+n*16+32768,work)
    def observe(self,aid,data):
        fields(data,('request_id','noise'),('request_id','noise'));rid=identifier(data['request_id'],'request_id')
        acquisition=self._get('acquisitions',aid);meta=acquisition.meta();settings=noise_settings(data['noise'])
        def work(job):
            job.progress('Sampling observation',0,1);record,_=self._observation(meta,acquisition.array('y_clean'),settings);job.check()
            job.commit(lambda:self._publish_observation(record));job.progress('Observation ready',1,1)
        return self._submit('observation',rid,dict(acquisition_id=aid,noise=settings),['observations'],len(acquisition.array('y_clean'))*64+16384,work)
    def _publish_observation(self,record):
        meta=record.meta();self._publish([('observations',meta['observation_id'],record)])
        return dict(observation_id=meta['observation_id'],acquisition_id=meta['acquisition_id'])
    def inference(self,oid):
        # Deliberately omits even acquisition identity, so no truth lookup is needed by inference.
        record=self._get('observations',oid);m=record.meta()
        return dict(observation_id=oid,operator_id=m['operator_id'],y=record.array('y'),sigma=m['noise']['sigma'])
    def reconstruct(self,data):
        fields(data,('request_id','observation_id','iterations','step'),('request_id','observation_id','iterations','step'))
        rid=identifier(data['request_id'],'request_id');oid=identifier(data['observation_id'],'observation_id')
        K=integer(data['iterations'],'iterations',1,5000);observation=self._get('observations',oid);om=observation.meta()
        acquisition=self._get('acquisitions',om['acquisition_id']);am=acquisition.meta();spec=self.specs[om['operator_id']]
        class NormInfo:
            def metadata(self): return am['operator']
        step=resolve_step(NormInfo(),data['step']);recid=uuid4().hex
        config=dict(observation_id=oid,operator_id=spec.geometry_hash,observation_hash=om['content_hash'],iterations=K,**step,
            initialization='zero',dtype='float64',solver_version='landweber_v1',checkpoint_policy='ceil_K_over_100_v1')
        config_hash=digest(config);inference=self.inference(oid)
        # Validation is wired outside the pure kernel and receives independent iterate copies.
        truth=self.snapshots.array(am['snapshot_id']).ravel()
        def work(job):
            op,hit=self.operator(spec,job);diagnostics=ImageDiagnostics(truth)
            def emit(k,x):
                diagnostics.record(k,x)
                if k%5==0 or k==K: job.progress('Reconstructing',k,K)
            result=landweber(op,inference['y'],step['omega'],K,job.event.is_set,emit)
            meta=dict(reconstruction_id=recid,configuration_hash=config_hash,configuration=config,observation_id=oid,operator_id=spec.geometry_hash,
                acquisition_id=om['acquisition_id'],snapshot_id=am['snapshot_id'],resolution=spec.resolution,shape=[spec.resolution]*2,flatten_order='C',dtype='float64',created_at=now(),
                history=result['history'],diagnostics=dict(label='Validation only — synthetic ground truth known',snapshot_id=am['snapshot_id'],history=diagnostics.history),
                checkpoint_indices=list(result['checkpoints']),operator_cache_hit=hit,
                warnings=[] if step['nonoscillatory_certified'] else ['Stable under conservative bound; nonoscillatory condition is not certified.'],
                **{k:result[k] for k in ('completed_iterations','requested_iterations','status','stop_reason','elapsed_seconds','checkpoint_stride','forward_applications','adjoint_applications')})
            record=Record.make(meta,**{f'checkpoint_{k}':v for k,v in result['checkpoints'].items()})
            def publish():
                self._publish([('reconstructions',recid,record)])
                return dict(reconstruction_id=recid,completed_iterations=result['completed_iterations'])
            # A cancellation arriving after all K updates does not relabel a completed result.
            with job.lock:
                if result['status']=='completed':
                    refs=publish();job.data.update(result=refs,status='completed',finished_at=now())
                else: job.commit(publish,'canceled')
            job.progress('Reconstruction '+result['status'],result['completed_iterations'],K)
        m,n=spec.shape
        workspace=spec.estimate_bytes()+n*8*110+(K+1)*1400+(m+n)*96+32768
        return self._submit('reconstruction',rid,dict(configuration_hash=config_hash,**config),['reconstructions'],workspace,work)
    def result(self,recid):
        record=self._get('reconstructions',recid);meta=record.meta();meta.pop('history');meta.pop('diagnostics')
        return {**meta,'pixels':record.array(f"checkpoint_{meta['completed_iterations']}").tolist()}
    def history(self,recid):
        meta=self._get('reconstructions',recid).meta()
        return {key:meta[key] for key in ('reconstruction_id','configuration_hash','history','diagnostics')}
    def checkpoint(self,recid,k):
        record=self._get('reconstructions',recid);meta=record.meta();integer(k,'iteration',0,meta['completed_iterations'])
        key=(recid,k)
        if k in meta['checkpoint_indices']: pixels=record.array(f'checkpoint_{k}');origin='base'
        else:
            with self.lock:
                if key not in self.inspections: raise InputError('Iteration needs replay. Submit a checkpoint request.','iteration','checkpoint_missing',404)
                payload,lease=self.inspections.pop(key);self.inspections[key]=(payload,lease);pixels=np.frombuffer(payload,dtype='<f8');origin='replayed'
        return dict(reconstruction_id=recid,configuration_hash=meta['configuration_hash'],iteration=k,shape=meta['shape'],flatten_order='C',dtype='float64',origin=origin,pixels=pixels.tolist())
    def checkpoint_request(self,recid,data):
        fields(data,('request_id','iteration'),('request_id','iteration'));rid=identifier(data['request_id'],'request_id')
        record=self._get('reconstructions',recid);meta=record.meta();k=integer(data['iteration'],'iteration',0,meta['completed_iterations'])
        try: return self.checkpoint(recid,k),200
        except InputError as exc:
            if exc.code!='checkpoint_missing': raise
        config=meta['configuration'];spec=self.specs[meta['operator_id']];inference=self.inference(meta['observation_id'])
        start=max(i for i in meta['checkpoint_indices'] if i<=k);initial=record.array(f'checkpoint_{start}')
        def work(job):
            op,_=self.operator(spec,job)
            values=replay(op,inference['y'],config['omega'],initial,start,k,job.check,job.progress);job.check()
            def publish():
                payload=values.tobytes();size=len(payload)
                if size>self.replay_limit: raise InputError('Replay cache capacity is too small.',code='resource_limit',status=507)
                with self.lock:
                    if (recid,k) in self.inspections:
                        old,lease=self.inspections.pop((recid,k));self.replay_bytes-=len(old);lease.close()
                    while self.inspections and self.replay_bytes+size>self.replay_limit:
                        _,(old,lease)=self.inspections.popitem(last=False);self.replay_bytes-=len(old);lease.close()
                    lease=self.budget.reserve(size);self.inspections[(recid,k)]=(payload,lease);self.replay_bytes+=size
                return dict(reconstruction_id=recid,configuration_hash=meta['configuration_hash'],iteration=k)
            job.commit(publish)
        return self._submit('replay',rid,dict(reconstruction_id=recid,configuration_hash=meta['configuration_hash'],iteration=k),[],spec.estimate_bytes()+spec.shape[0]*32+spec.shape[1]*64,work),202
