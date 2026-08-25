import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

from app.observability.context import get_context


SENSITIVE_HEADERS = {"authorization", "x-api-key", "cookie", "set-cookie", "proxy-authorization"}
SENSITIVE_QUERY_PARAMETERS = {"password", "token", "secret", "api_key", "access_token", "refresh_token", "signature"}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def redact_headers(headers):
    return {key: "[REDACTED]" if key.lower() in SENSITIVE_HEADERS else value for key, value in headers.items()}


def redact_query(query):
    if not query:
        return query
    values = []
    for item in query.split("&"):
        key = item.split("=", 1)[0]
        values.append(f"{key}=[REDACTED]" if key.lower() in SENSITIVE_QUERY_PARAMETERS else item)
    return "&".join(values)


def _safe(value):
    if isinstance(value, str):
        return _CONTROL_CHARACTERS.sub("", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            **get_context(),
        }
        payload.update({key: _safe(value) for key, value in getattr(record, "fields", {}).items()})
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["message"] = _safe(str(record.exc_info[1]))
        return json.dumps(payload, default=str)


def configure_logging(level=None, json_logs=None):
    level_name = str(level or os.getenv("LOG_LEVEL", "INFO")).upper()
    numeric_level = getattr(logging, level_name, logging.INFO)
    use_json = json_logs if json_logs is not None else os.getenv("LOG_JSON", "true").lower() in {"1", "true", "yes", "on"}
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if use_json else logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger("gateforge")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)
    root.propagate = False
    return root


def log_event(logger, level, event, **fields):
    logger.log(level, event, extra={"event": event, "fields": fields})