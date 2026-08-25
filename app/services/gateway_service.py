import logging
import time
import uuid
from contextlib import contextmanager

import requests
from flask import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

from app.database.connection import SessionLocal
from app.gateway.proxy import forward_request, response_parts
from app.gateway.proxy import build_upstream_url
from app.gateway.resolver import GatewayResolutionError, resolve_gateway_request
from app.services.api_key_service import validate_api_key
from app.services.rate_limit_service import RateLimitConfigurationError
from app.services.policy_service import PolicyConfigurationError, evaluate_policy, resolve_effective_policy
from app.services.traffic_service import record_traffic
from app.sockets.events import emit_gateway_error, emit_rate_limit_event, emit_traffic_event

logger = logging.getLogger("gateforge.gateway")


def request_id_from(request: Request) -> str:
    value = request.headers.get("X-Request-ID", "")
    if value and len(value) <= 128 and all(character.isprintable() and character not in "\r\n" for character in value):
        return value
    return str(uuid.uuid4())


def error_response(message: str, status: int, request_id: str) -> tuple[dict, int]:
    return {"error": message, "status": status, "request_id": request_id}, status


def _rate_headers(decision) -> list[tuple[str, str]]:
    headers = []
    if decision.limit is not None:
        prefix = "X-Quota" if decision.quota else "X-RateLimit"
        headers.extend([(f"{prefix}-Limit", str(decision.limit)), (f"{prefix}-Remaining", str(decision.remaining)), (f"{prefix}-Reset", str(decision.reset_at))])
        if decision.retry_after is not None:
            headers.append(("Retry-After", str(decision.retry_after)))
    return headers


def _record(session, **values):
    record_traffic(session, **values)


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
    api_key = None
    resolved = None
    try:
        api_key = validate_api_key(session, plaintext_key)
        if api_key is None:
            status = 401
            return error_response("Invalid API key", status, request_id)
        resolved = resolve_gateway_request(session, api_slug, request_path, flask_request.method, api_key.user_id)
        policy = resolve_effective_policy(session, api_key, resolved.api, resolved.route)
        decision = evaluate_policy(session, api_key.id, policy)
        if not decision.allowed:
            status = 429
            _record(
                session, request_id=request_id, api_id=resolved.api.id, route_id=resolved.route.id,
                api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method,
                path=flask_request.path, upstream_url=None, status_code=429,
                duration_ms=int((time.perf_counter() - started) * 1000), request_size=flask_request.content_length or 0,
                response_size=0, rate_limit_allowed=False, rate_limit_remaining=decision.remaining,
                plan_id=policy.plan.id, limit_type=decision.policy_type,
                rate_limit_limit=decision.limit if not decision.quota else None,
                quota_limit=decision.limit if decision.quota else None,
                quota_remaining=decision.remaining if decision.quota else None, error_type="rate_limit",
            )
            emit_rate_limit_event(
                request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id,
                route_id=resolved.route.id, api_key_id=api_key.id, limit_type=decision.policy_type,
                limit=decision.limit, remaining=decision.remaining, retry_after=decision.retry_after,
            )
            message = "Daily quota exceeded" if decision.quota and decision.policy_type == "daily" else "Monthly quota exceeded" if decision.quota else "Rate limit exceeded"
            return {"error": message, "status": 429, "request_id": request_id, "limit_type": decision.policy_type, "retry_after": decision.retry_after}, 429, _rate_headers(decision)
        response = forward_request(resolved, flask_request, request_id)
        body, status, headers = response_parts(response)
        headers.extend(_rate_headers(decision))
        _record(
            session, request_id=request_id, api_id=resolved.api.id, route_id=resolved.route.id,
            api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method,
            path=flask_request.path, upstream_url=build_upstream_url(resolved.api.base_url, resolved.target_path),
            status_code=status, duration_ms=int((time.perf_counter() - started) * 1000),
            request_size=flask_request.content_length or 0, response_size=len(body),
            rate_limit_allowed=True, rate_limit_remaining=decision.remaining,
            plan_id=policy.plan.id, limit_type=decision.policy_type,
            rate_limit_limit=decision.limit if not decision.quota else None,
            quota_limit=decision.limit if decision.quota else None,
            quota_remaining=decision.remaining if decision.quota else None, error_type=None,
        )
        emit_traffic_event(
            request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id,
            route_id=resolved.route.id, api_key_id=api_key.id, method=flask_request.method,
            path=flask_request.path, status_code=status, duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return body, status, headers
    except GatewayResolutionError as error:
        status = error.status
        if api_key is not None:
            _record(
                session, request_id=request_id, api_id=None, route_id=None, api_key_id=api_key.id,
                user_id=api_key.user_id, method=flask_request.method, path=flask_request.path,
                upstream_url=None, status_code=status, duration_ms=int((time.perf_counter() - started) * 1000),
                request_size=flask_request.content_length or 0, response_size=0,
                rate_limit_allowed=None, rate_limit_remaining=None, error_type="gateway_error",
            )
            emit_gateway_error(request_id=request_id, api_id=None, owner_id=api_key.user_id, error_type="gateway_error", status_code=status)
        return error_response(error.message, error.status, request_id)
    except (RateLimitConfigurationError, PolicyConfigurationError):
        status = 500
        return error_response("Rate limit configuration error", status, request_id)
    except requests.Timeout:
        status = 504
        if api_key is not None and resolved is not None:
            _record_failure(session, resolved, api_key, flask_request, request_id, 504, "upstream_timeout", started)
            emit_gateway_error(request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, error_type="upstream_timeout", status_code=504)
        return error_response("Upstream service timed out", 504, request_id)
    except (requests.RequestException, ValueError):
        status = 502
        if api_key is not None and resolved is not None:
            _record_failure(session, resolved, api_key, flask_request, request_id, 502, "upstream_connection", started)
            emit_gateway_error(request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, error_type="upstream_connection", status_code=502)
        return error_response("Unable to reach upstream service", 502, request_id)
    except SQLAlchemyError:
        status = 503
        return error_response("Gateway database operation failed", status, request_id)
    finally:
        duration = time.perf_counter() - started
        logger.info(
            "Gateway request request_id=%s api=%s route=/%s method=%s status=%s duration=%.3fs",
            request_id, api_slug, request_path, flask_request.method, status, duration,
        )
        session.close()


def _record_failure(session, resolved, api_key, flask_request, request_id, status, error_type, started):
    _record(
        session, request_id=request_id, api_id=resolved.api.id, route_id=resolved.route.id,
        api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method,
        path=flask_request.path, upstream_url=None, status_code=status,
        duration_ms=int((time.perf_counter() - started) * 1000), request_size=flask_request.content_length or 0,
        response_size=0, rate_limit_allowed=True, rate_limit_remaining=None, error_type=error_type,
    )
