"""Single numerical worker, cooperative cancellation and conservative reservations."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import logging
from threading import Condition, Event, RLock, Thread
from uuid import uuid4
from ..images import InputError


def now(): return datetime.now(timezone.utc).isoformat()


class MemoryBudget:
    def __init__(self, limit): self.limit=limit;self.used=0;self.lock=RLock()
    def reserve(self,size):
        with self.lock:
            if self.used+size>self.limit:
                raise InputError('Tracked payload/workspace budget exceeded. Try fewer angles, detectors or iterations; existing records are preserved.',code='resource_limit',status=507)
            self.used+=size
        return Lease(self,size)


class Lease:
    def __init__(self,budget,size): self.budget=budget;self.size=size
    def close(self):
        with self.budget.lock:
            self.budget.used-=self.size;self.size=0


class Canceled(Exception): pass


class Job:
    def __init__(self,kind,request_id,inputs,work,cleanup):
        self.lock=RLock();self.event=Event();self.work=work;self.cleanup=cleanup
        self.data=dict(job_id=uuid4().hex,request_id=request_id,kind=kind,inputs=deepcopy(inputs),status='queued',stage='Queued',
            completed=0,total=None,created_at=now(),started_at=None,finished_at=None,cancellation_requested=False,result=None,error=None,warnings=[])
    def check(self):
        if self.event.is_set(): raise Canceled()
    def progress(self,stage,completed,total):
        with self.lock: self.data.update(stage=stage,completed=completed,total=total)
    def commit(self,publish,status='completed'):
        # Cancellation and publication share a lock: no partial acquisition is visible.
        with self.lock:
            if status=='completed': self.check()
            result=publish()
            self.data.update(result=result,status=status,finished_at=now())
            return result
    def snapshot(self):
        with self.lock: return deepcopy(self.data)


class JobRunner:
    def __init__(self,pending_limit=4,registry_limit=128):
        self.condition=Condition();self.pending=deque();self.jobs={};self.pending_limit=pending_limit;self.registry_limit=registry_limit;self.thread=None;self.stopping=False
    def submit(self,kind,request_id,inputs,work,cleanup=lambda:None):
        with self.condition:
            if self.stopping or len(self.pending)>=self.pending_limit:
                raise InputError('Numerical queue is full. Wait for a job or cancel queued work.',code='queue_full',status=429)
            if len(self.jobs)>=self.registry_limit:
                raise InputError('Job registry capacity reached. Restart to clear this process after inspecting results.',code='storage_limit',status=507)
            job=Job(kind,request_id,inputs,work,cleanup);self.jobs[job.data['job_id']]=job;self.pending.append(job)
            if self.thread is None:
                self.thread=Thread(target=self._run,name='ct-numerical-worker',daemon=True);self.thread.start()
            self.condition.notify()
            return job.snapshot()
    def get(self,job_id):
        with self.condition:
            if job_id not in self.jobs: raise InputError('Job unavailable in this server process.','job_id','not_found',404)
            return self.jobs[job_id]
    def cancel(self,job_id):
        job=self.get(job_id)
        with self.condition,job.lock:
            if job.data['status'] not in ('completed','canceled','failed'):
                job.event.set();job.data['cancellation_requested']=True
                if job in self.pending:
                    self.pending.remove(job);job.cleanup();job.work=None
                    job.data.update(status='canceled',stage='Canceled before starting',finished_at=now())
            return job.snapshot()
    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.pending or self.stopping)
                if self.stopping and not self.pending: return
                job=self.pending.popleft()
            try:
                with job.lock: job.data.update(status='running',started_at=now())
                job.check();job.work(job)
                with job.lock:
                    if job.data['status']=='running': job.data.update(status='completed',finished_at=now())
            except Canceled:
                with job.lock: job.data.update(status='canceled',finished_at=now())
            except Exception as exc:
                if not isinstance(exc,InputError): logging.exception('Numerical job failed: %s',job.data['job_id'])
                with job.lock:
                    job.data.update(status='failed',finished_at=now(),error=dict(code=exc.code if isinstance(exc,InputError) else 'server_error',
                        message=str(exc) if isinstance(exc,InputError) else 'Numerical job failed; see local server log.',field=exc.field if isinstance(exc,InputError) else None,request_id=job.data['request_id']))
            finally:
                job.cleanup();job.work=None
    def close(self):
        for job_id in list(self.jobs): self.cancel(job_id)
        with self.condition: self.stopping=True;self.condition.notify_all()
        if self.thread: self.thread.join(timeout=5)
