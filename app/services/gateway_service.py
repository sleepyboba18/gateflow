import logging
import time
import uuid
from contextlib import contextmanager

import requests
from flask import Request
from sqlalchemy import select

from app.database.connection import SessionLocal
from app.gateway.proxy import forward_request, response_parts
from app.gateway.resolver import GatewayResolutionError, resolve_gateway_request
from app.models.api_key import APIKey
from app.services.api_key_service import validate_api_key

logger = logging.getLogger("gateforge.gateway")


def request_id_from(request: Request) -> str:
    value = request.headers.get("X-Request-ID", "")
    if value and len(value) <= 128 and all(character.isprintable() and character not in "\r\n" for character in value):
        return value
    return str(uuid.uuid4())


def error_response(message: str, status: int, request_id: str) -> tuple[dict, int]:
    return {"error": message, "status": status, "request_id": request_id}, status


def handle_gateway_request(api_slug: str, request_path: str, flask_request: Request, request_id: str):
    started = time.perf_counter()
    status = 500
    plaintext_key = flask_request.headers.get("X-API-Key", "")
    if not plaintext_key:
        status = 401
        return error_response("API key required", status, request_id)
    if SessionLocal is None:
        return error_response("Unable to authenticate gateway request", 503, request_id)
    session = SessionLocal()
    try:
        api_key = validate_api_key(session, plaintext_key)
        if api_key is None:
            status = 401
            return error_response("Invalid API key", status, request_id)
        resolved = resolve_gateway_request(session, api_slug, request_path, flask_request.method, api_key.user_id)
        response = forward_request(resolved, flask_request, request_id)
        body, status, headers = response_parts(response)
        return body, status, headers
    except GatewayResolutionError as error:
        status = error.status
        return error_response(error.message, error.status, request_id)
    except requests.Timeout:
        status = 504
        return error_response("Upstream service timed out", 504, request_id)
    except (requests.RequestException, ValueError):
        status = 502
        return error_response("Unable to reach upstream service", 502, request_id)
    finally:
        duration = time.perf_counter() - started
        logger.info(
            "Gateway request request_id=%s api=%s route=/%s method=%s status=%s duration=%.3fs",
            request_id, api_slug, request_path, flask_request.method, status, duration,
        )
        session.close()
