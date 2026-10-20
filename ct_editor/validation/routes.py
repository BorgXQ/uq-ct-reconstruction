from flask import Blueprint,request,jsonify,send_file
from ..images import InputError
from ..ct.geometry import integer
from .service import plain


def routes(service,artifacts):
    bp=Blueprint('validation',__name__)
    def body():
        if not request.is_json:raise InputError('Send application/json.',code='unsupported_format',status=415)
        value=request.get_json()
        if not isinstance(value,dict):raise InputError('Expected an object.')
        return value
    @bp.post('/api/validation-studies/preflight')
    def preflight():return jsonify(service.preflight(body()))
    @bp.post('/api/validation-studies')
    def create():return jsonify(service.submit(body())),202
    @bp.get('/api/validation-studies')
    def listing():
        with service.lock:
            return jsonify(studies=[service.response(sid) for sid in service.records],pending=plain(list(service.pending.values())))
    @bp.get('/api/validation-studies/<sid>')
    def get(sid):return jsonify(service.response(sid,request.args.get('arrays')=='1'))
    @bp.get('/api/validation-studies/<sid>/trials')
    def trials(sid):
        try:offset=int(request.args.get('offset',0));limit=int(request.args.get('limit',100))
        except ValueError:raise InputError('Invalid pagination.')
        integer(offset,'offset',0,1000000);integer(limit,'limit',1,500);r=service.get(sid)
        return jsonify(study_id=sid,total=len(r['ledger']),trials=plain(r['ledger'][offset:offset+limit]))
    @bp.get('/api/validation-studies/<sid>/pixels/<int:index>')
    def pixel(sid,index):return jsonify(service.pixel(sid,index))
    @bp.get('/api/validation-studies/<sid>/representatives/<key>')
    def representative(sid,key):
        r=service.get(sid)
        if key not in r['representatives']:raise InputError('Trial not retained. Request explicit replay.',code='not_found',status=404)
        return jsonify(plain(r['representatives'][key]))
    @bp.post('/api/validation-studies/<sid>/inspection-requests')
    def inspect(sid):return jsonify(artifacts.replay(sid,body())),202
    @bp.post('/api/parameter-studies/preflight')
    def grid_preflight():return jsonify(service.grid(body(),True))
    @bp.post('/api/parameter-studies')
    def grid():return jsonify(service.grid(body())),202
    @bp.get('/api/parameter-studies/<gid>')
    def grid_get(gid):
        if gid not in service.grids:raise InputError('Unknown grid.',code='not_found',status=404)
        return jsonify(plain(service.grids[gid]))
    @bp.post('/api/exports')
    def export():
        d=body();return jsonify(artifacts.export(d['study_id'],d.get('profile','numerical'),d.get('request_id','export'),d.get('trial',0))),202
    @bp.get('/api/exports/<eid>')
    def export_get(eid):
        if eid not in artifacts.exports:raise InputError('Artifact not ready or unavailable.',code='not_found',status=404)
        return jsonify(artifacts.exports[eid])
    @bp.get('/api/exports/<eid>/download')
    def download(eid):
        if eid not in artifacts.exports:raise InputError('Artifact unavailable.',code='not_found',status=404)
        return send_file(service.directory/(eid+'.zip'),as_attachment=True,download_name=f"ct-{artifacts.exports[eid]['profile']}-{eid[:8]}.zip")
    @bp.post('/api/imports')
    def restore():
        request.max_content_length=service.disk_limit
        if request.mimetype!='application/zip':raise InputError('Upload a numerical ZIP bundle.',code='unsupported_format',status=415)
        raw=request.get_data();return jsonify(artifacts.restore(raw,'import')),202
    @bp.get('/api/imports/<iid>')
    def imported(iid):
        if iid not in artifacts.imports:raise InputError('Imported run unavailable.',code='not_found',status=404)
        return jsonify(artifacts.imports[iid])
    @bp.post('/api/imports/<iid>/replay-requests')
    def replay(iid):
        if iid not in artifacts.imports:raise InputError('Imported run unavailable.',code='not_found',status=404)
        return jsonify(artifacts.replay(artifacts.imports[iid]['study_id'],body())),202
    @bp.get('/api/replays/<rid>')
    def replay_result(rid):
        if rid not in service.replays:raise InputError('Replay unavailable.',code='not_found',status=404)
        return jsonify(service.replays[rid])
    return bp
