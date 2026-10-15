"""Same-origin M2 endpoints; GET never launches numerical work."""
from flask import Blueprint, jsonify, request
from ..images import InputError
from .geometry import fields


def routes(service):
    bp=Blueprint('ct',__name__)
    def body():
        if not request.is_json: raise InputError('Send application/json.',code='unsupported_format',status=415)
        data=request.get_json()
        if not isinstance(data,dict): raise InputError('Expected a JSON object.')
        return data
    @bp.post('/api/acquisitions')
    def acquire(): return jsonify(service.acquire(body())),202
    @bp.get('/api/acquisitions')
    def acquisitions(): return jsonify(acquisitions=service.list('acquisitions'))
    @bp.get('/api/acquisitions/<key>')
    def acquisition(key): return jsonify(service._get('acquisitions',key).response())
    @bp.post('/api/acquisitions/<key>/observations')
    def observe(key): return jsonify(service.observe(key,body())),202
    @bp.get('/api/observations')
    def observations():
        records=service.list('observations');aid=request.args.get('acquisition_id')
        return jsonify(observations=[r for r in records if not aid or r['acquisition_id']==aid])
    @bp.get('/api/observations/<key>')
    def observation(key): return jsonify(service._get('observations',key).response())
    @bp.post('/api/reconstructions')
    def reconstruct(): return jsonify(service.reconstruct(body())),202
    @bp.get('/api/reconstructions')
    def reconstructions(): return jsonify(reconstructions=service.list('reconstructions'))
    @bp.get('/api/reconstructions/<key>')
    def reconstruction(key): return jsonify(service.result(key))
    @bp.get('/api/reconstructions/<key>/history')
    def history(key): return jsonify(service.history(key))
    @bp.get('/api/reconstructions/<key>/checkpoints/<int:k>')
    def checkpoint(key,k): return jsonify(service.checkpoint(key,k))
    @bp.post('/api/reconstructions/<key>/checkpoint-requests')
    def replay_request(key):
        result,status=service.checkpoint_request(key,body());return jsonify(result),status
    @bp.get('/api/jobs/<key>')
    def job(key): return jsonify(service.runner.get(key).snapshot())
    @bp.get('/api/jobs')
    def jobs():
        with service.runner.condition:
            return jsonify(jobs=[job.snapshot() for job in service.runner.jobs.values()])
    @bp.post('/api/jobs/<key>/cancel')
    def cancel(key):
        if request.content_length: fields(body(),())
        return jsonify(service.runner.cancel(key))
    return bp
