"""Explicit exact-analysis jobs; reads never launch numerical work."""
from flask import Blueprint,request,jsonify
from ..images import InputError


def routes(service):
    bp=Blueprint('exact',__name__)
    def body():
        if not request.is_json: raise InputError('Send application/json.',code='unsupported_format',status=415)
        data=request.get_json()
        if not isinstance(data,dict): raise InputError('Expected an object.')
        return data
    @bp.post('/api/exact-analyses')
    def create(): return jsonify(service.submit(body())),202
    @bp.post('/api/reconstructions/<rid>/exact-analyses')
    def attach(rid): return jsonify(service.submit(body(),rid)),202
    @bp.get('/api/exact-analyses')
    def listing(): return jsonify(exact_analyses=service.ct.list('exact_analyses'))
    @bp.get('/api/exact-analyses/<aid>')
    def result(aid): return jsonify(service.response(aid))
    @bp.get('/api/exact-analyses/<aid>/stopping-history')
    def history(aid):
        meta=service.ct._get('exact_analyses',aid).meta()
        return jsonify(analysis_id=aid,configuration_hash=meta['configuration_hash'],selection=meta['selection'],history=meta['history'])
    @bp.post('/api/exact-analyses/<aid>/inspection-requests')
    def inspect(aid): return jsonify(service.inspect(aid,body())),202
    @bp.get('/api/exact-views/<vid>')
    def view(vid): return jsonify(service.response(vid,'exact_views'))
    @bp.post('/api/exact-analyses/<aid>/diagnostics')
    def diagnostics(aid): return jsonify(service.diagnostics(aid,body())),202
    @bp.get('/api/exact-diagnostics')
    def diagnostic_list(): return jsonify(exact_diagnostics=service.ct.list('exact_diagnostics'))
    @bp.get('/api/exact-diagnostics/<did>')
    def diagnostic(did): return jsonify(service.ct._get('exact_diagnostics',did).response())
    @bp.post('/api/exact-analyses/<aid>/cap-reconstruction')
    def cap(aid): return jsonify(service.cap(aid,body())),202
    @bp.get('/api/exact-analyses/<aid>/joint-region')
    def joint(aid):
        meta=service.ct._get('exact_analyses',aid).meta()
        return jsonify(meta.get('joint_region',dict(status='joint_region_unavailable',reason='no_completed_analysis')))
    return bp
