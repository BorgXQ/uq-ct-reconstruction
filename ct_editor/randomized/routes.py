"""Explicit numerical actions; GET requests only retrieve retained artifacts."""
from flask import Blueprint,request,jsonify
from ..images import InputError


def routes(service):
    bp=Blueprint('randomized',__name__)
    def body():
        if not request.is_json:raise InputError('Send application/json.',code='unsupported_format',status=415)
        data=request.get_json()
        if not isinstance(data,dict):raise InputError('Expected an object.')
        return data
    @bp.post('/api/randomized-preparations')
    def prepare():return jsonify(service.submit(body(),prepare=True)),202
    @bp.post('/api/randomized-analyses/<aid>/finalize')
    def finalize(aid):return jsonify(service.finalize(aid,body())),202
    @bp.post('/api/randomized-analyses')
    def create():return jsonify(service.submit(body())),202
    @bp.post('/api/uncertainty-comparisons')
    def compare():
        data=body()
        if not data.get('exact_analysis_id'):raise InputError('Select a completed exact analysis.')
        return jsonify(service.submit(data,exact_id=data['exact_analysis_id'])),202
    @bp.post('/api/reconstructions/<rid>/randomized-analyses')
    def attach(rid):return jsonify(service.submit(body(),recid=rid)),202
    @bp.get('/api/randomized-analyses')
    def listing():return jsonify(randomized_analyses=service.ct.list('randomized_analyses'))
    @bp.get('/api/randomized-analyses/<aid>')
    @bp.get('/api/uncertainty-comparisons/<aid>')
    def result(aid):return jsonify(service.response(aid))
    @bp.get('/api/randomized-analyses/<aid>/stopping-history')
    def history(aid):
        meta=service.ct._get('randomized_analyses',aid).meta()
        return jsonify(analysis_id=aid,selection=meta['selection'],history=meta['history'])
    @bp.post('/api/randomized-analyses/<aid>/inspection-requests')
    def inspect(aid):return jsonify(service.inspect(aid,body())),202
    @bp.get('/api/randomized-views/<vid>')
    def view(vid):return jsonify(service.response(vid,'randomized_views'))
    @bp.post('/api/randomized-analyses/<aid>/diagnostics')
    def diagnostics(aid):return jsonify(service.diagnostics(aid,body())),202
    @bp.get('/api/approximation-diagnostics')
    def diagnostic_list():return jsonify(approximation_diagnostics=service.ct.list('approximation_diagnostics'))
    @bp.get('/api/approximation-diagnostics/<did>')
    def diagnostic(did):return jsonify(service.ct._get('approximation_diagnostics',did).response())
    @bp.post('/api/randomized-analyses/<aid>/stopping-comparisons')
    def own(aid):return jsonify(service.own_stopping(aid,body())),202
    @bp.get('/api/stopping-comparisons/<cid>')
    def own_result(cid):return jsonify(service.ct._get('stopping_comparisons',cid).response())
    return bp
