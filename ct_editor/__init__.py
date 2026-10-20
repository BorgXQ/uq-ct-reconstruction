"""Local drawing, measurement and reconstruction app factory."""
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .images import InputError, parse_request, convert, serialize
from .store import SnapshotStore
from .ct.jobs import MemoryBudget
from .ct.service import CTService
from .ct.routes import routes
from .exact.service import ExactService
from .exact.routes import routes as exact_routes
from .randomized.service import RandomizedService
from .randomized.routes import routes as randomized_routes
from .validation.service import ValidationService
from .validation.artifacts import Artifacts
from .validation.routes import routes as validation_routes


def create_app(config=None):
    root = Path(__file__).resolve().parent.parent
    app = Flask(__name__, static_folder=str(root / "static"), template_folder=str(root / "templates"))
    app.config.update(MAX_CONTENT_LENGTH=1024**2, SNAPSHOT_LIMIT=50, SNAPSHOT_BYTE_LIMIT=64 * 1024**2)
    if config:
        app.config.update(config)
    budget = MemoryBudget(app.config.get('CT_MEMORY_BYTES', 512 * 1024**2))
    store = SnapshotStore(app.config["SNAPSHOT_LIMIT"], app.config["SNAPSHOT_BYTE_LIMIT"], budget)
    app.extensions["snapshot_store"] = store
    service = CTService(store, budget, app.config)
    app.extensions['ct_service'] = service
    app.register_blueprint(routes(service))
    exact = ExactService(service, app.config)
    app.extensions['exact_service'] = exact
    app.register_blueprint(exact_routes(exact))
    randomized = RandomizedService(exact, app.config)
    app.extensions['randomized_service'] = randomized
    app.register_blueprint(randomized_routes(randomized))
    validation = ValidationService(randomized, app.config)
    artifacts = Artifacts(validation)
    app.extensions['validation_service'] = validation
    app.extensions['validation_artifacts'] = artifacts
    app.register_blueprint(validation_routes(validation, artifacts))

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ready", milestone="M6")

    def capture():
        if not request.is_json:
            raise InputError("Send application/json.", code="unsupported_format", status=415)
        raw, resolution, identity = parse_request(request.get_json())
        metadata, payload = convert(raw, resolution)
        return raw, identity, metadata, payload

    @app.post("/api/images/preview")
    def preview():
        _, identity, metadata, payload = capture()
        return jsonify(**serialize(metadata, payload), **identity, snapshot_id=None, created_at=None)

    @app.post("/api/snapshots")
    def freeze():
        raw, identity, metadata, payload = capture()
        return jsonify(**store.insert(metadata, payload, raw, identity), **identity), 201

    @app.get("/api/snapshots")
    def snapshots():
        return jsonify(snapshots=store.list(), capacity=store.max_count, byte_capacity=store.max_bytes)

    @app.get("/api/snapshots/<snapshot_id>")
    def snapshot(snapshot_id):
        return jsonify(store.get(snapshot_id))

    def error_response(code, message, status, field=None):
        data = None
        if request.content_length is not None and request.content_length <= app.config["MAX_CONTENT_LENGTH"] and request.is_json:
            data = request.get_json(silent=True)
        rid = data.get("request_id") if isinstance(data, dict) else None
        if not isinstance(rid, str) or len(rid) > 128:
            rid = None
        return jsonify(error={"code": code, "message": message, "field": field, "request_id": rid}), status

    @app.errorhandler(InputError)
    def invalid(exc):
        return error_response(exc.code, str(exc), exc.status, exc.field)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        codes = {413: "body_too_large", 400: "invalid_json", 404: "not_found", 415: "unsupported_format"}
        return error_response(codes.get(exc.code, "http_error"), exc.description, exc.code)

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception("Unexpected request failure")
        return error_response("server_error", "The server could not complete this request. Please retry.", 500)

    @app.after_request
    def no_cache(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app
