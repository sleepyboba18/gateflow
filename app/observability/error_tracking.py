import logging

from flask import g, request

from app.observability.context import set_context
from app.observability.logger import log_event


def handle_exception(logger, exception):
    current_id = getattr(g, "request_id", None)
    set_context(request_id=current_id)
    log_event(logger, logging.ERROR, "application.exception", exception_type=type(exception).__name__, message=str(exception), endpoint=request.endpoint, method=request.method)