import time
import uuid

from flask import g, request

from app.observability.context import clear_context, set_context
from app.observability.logger import log_event, redact_query


def valid_request_id(value):
    return bool(value and len(value) <= 128 and all(character.isprintable() and not character.isspace() for character in value))


def request_id(value):
    return value if valid_request_id(value) else str(uuid.uuid4())


def init_request_logging(app, logger):
    @app.before_request
    def request_started():
        clear_context()
        current_id = request_id(request.headers.get("X-Request-ID"))
        g.request_id = current_id
        g.request_started_at = time.perf_counter()
        set_context(request_id=current_id, client_ip=request.remote_addr)
        log_event(logger, 20, "request.started", method=request.method, path=request.path, query=redact_query(request.query_string.decode("utf-8", "replace")))

    @app.after_request
    def request_completed(response):
        current_id = getattr(g, "request_id", str(uuid.uuid4()))
        response.headers["X-GateForge-Request-ID"] = current_id
        duration = (time.perf_counter() - getattr(g, "request_started_at", time.perf_counter())) * 1000
        log_event(logger, 20, "request.completed", method=request.method, path=request.path, status_code=response.status_code, duration_ms=round(duration, 2))
        clear_context()
        return response