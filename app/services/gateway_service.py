import logging
import time

import requests
from flask import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

from app.database.connection import SessionLocal
from app.gateway.proxy import forward_request, response_parts
from app.gateway.proxy import build_upstream_url
from app.gateway.resolver import GatewayResolutionError, resolve_gateway_request
from app.services.api_key_service import authenticate_api_key, client_ip
from app.services.rate_limit_service import RateLimitConfigurationError
from app.services.policy_service import PolicyConfigurationError, evaluate_policy, resolve_effective_policy
from app.services.traffic_service import record_traffic
from app.sockets.events import emit_gateway_error, emit_rate_limit_event, emit_traffic_event
from app.services.gateway_policy_service import resolve_gateway_policy
from app.services.scope_service import check_scope_access
from app.gateway.request_policy import apply_request_policy
from app.gateway.response_policy import apply_response_policy
from app.services.schema_service import get_effective_schema, validate_payload
from app.gateway.reliability import request_with_retries
from app.services.circuit_breaker_service import can_request, get_effective_breaker, record_failure, record_success
from app.services.upstream_health_service import record_result
from app.sockets.events import emit_circuit_event, emit_health_event
from app.observability.request_logging import request_id as validated_request_id

logger = logging.getLogger("gateforge.gateway")


def request_id_from(request: Request) -> str:
    return validated_request_id(request.headers.get("X-Request-ID"))


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


def handle_gateway_request(api_slug: str, request_path: str, flask_request: Request, request_id: str, version: str | None = None):
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
        auth_result = authenticate_api_key(
            session, plaintext_key, source_ip=client_ip(flask_request),
            origin=flask_request.headers.get("Origin"), request_id=request_id,
            user_agent=flask_request.headers.get("User-Agent"),
        )
        api_key = auth_result.api_key
        if api_key is None:
            status = 401
            message = "API key expired" if auth_result.reason == "expired" else "Invalid API key"
            return error_response(message, status, request_id)
        resolved = resolve_gateway_request(session, api_slug, request_path, flask_request.method, api_key.user_id, version)
        if not check_scope_access(session, api_key.id, resolved.api.id, resolved.route.id):
            status = 403
            _record(session, request_id=request_id, api_id=resolved.api.id, route_id=resolved.route.id, api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method, path=flask_request.path, upstream_url=None, status_code=403, duration_ms=int((time.perf_counter() - started) * 1000), request_size=flask_request.content_length or 0, response_size=0, rate_limit_allowed=None, rate_limit_remaining=None, scope_authorized=False, policy_allowed=None, policy_error="scope", error_type="authorization")
            emit_gateway_error(request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, error_type="authorization", status_code=403)
            return error_response("Insufficient scope", 403, request_id)
        gateway_policy = resolve_gateway_policy(session, resolved.api.id, resolved.route.id)
        _, policy_error = apply_request_policy({}, flask_request, gateway_policy, request_id, api_slug, resolved.route.path)
        if policy_error:
            status = 413 if "exceeds" in policy_error else 400
            _record(session, request_id=request_id, api_id=resolved.api.id, route_id=resolved.route.id, api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method, path=flask_request.path, upstream_url=None, status_code=status, duration_ms=int((time.perf_counter() - started) * 1000), request_size=flask_request.content_length or 0, response_size=0, rate_limit_allowed=None, rate_limit_remaining=None, scope_authorized=True, policy_allowed=False, policy_error=policy_error, error_type="request_policy")
            emit_gateway_error(request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, error_type="request_policy", status_code=status)
            return error_response(policy_error, status, request_id)
        if resolved.version is not None:
            schema = get_effective_schema(session, resolved.api.id, resolved.version.id, resolved.route.id, "request")
            if schema is not None:
                payload = flask_request.get_json(silent=True) if flask_request.get_data(cache=True) else None
                result = validate_payload(schema, payload, flask_request.content_type, bool(flask_request.get_data(cache=True)))
                if not result.valid:
                    status = result.status
                    _record(session, request_id=request_id, api_id=resolved.api.id, api_version_id=resolved.version.id, route_id=resolved.route.id, api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method, path=flask_request.path, upstream_url=None, status_code=status, duration_ms=int((time.perf_counter() - started) * 1000), request_size=flask_request.content_length or 0, response_size=0, rate_limit_allowed=None, rate_limit_remaining=None, scope_authorized=True, policy_allowed=True, policy_error=result.message, error_type="content_type" if status == 415 else "request_validation")
                    emit_gateway_error(request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, error_type="content_type" if status == 415 else "request_validation", status_code=status)
                    body = {"error": result.message, "status": status, "request_id": request_id}
                    if result.errors: body["validation_errors"] = list(result.errors)
                    return body, status, []
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
                quota_remaining=decision.remaining if decision.quota else None, scope_authorized=True, policy_allowed=True, error_type="rate_limit",
            )
            emit_rate_limit_event(
                request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id,
                route_id=resolved.route.id, api_key_id=api_key.id, limit_type=decision.policy_type,
                limit=decision.limit, remaining=decision.remaining, retry_after=decision.retry_after,
            )
            message = "Daily quota exceeded" if decision.quota and decision.policy_type == "daily" else "Monthly quota exceeded" if decision.quota else "Rate limit exceeded"
            return {"error": message, "status": 429, "request_id": request_id, "limit_type": decision.policy_type, "retry_after": decision.retry_after}, 429, _rate_headers(decision)
        breaker = get_effective_breaker(session, resolved.api.id, resolved.route.id)
        circuit = can_request(session, resolved.api.id, resolved.route.id)
        if circuit.changed:
            session.commit()
            emit_circuit_event(api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, previous_state=circuit.previous_state, state=circuit.state)
        if not circuit.allowed:
            status = 503
            retry_after = str(circuit.retry_after or 1)
            _record(session, request_id=request_id, api_id=resolved.api.id, route_id=resolved.route.id, api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method, path=flask_request.path, upstream_url=None, status_code=status, duration_ms=int((time.perf_counter() - started) * 1000), request_size=flask_request.content_length or 0, response_size=0, rate_limit_allowed=True, rate_limit_remaining=decision.remaining, circuit_state=circuit.state, retry_count=0, upstream_attempts=0, error_type="circuit_open")
            return {"error": "Upstream service temporarily unavailable", "status": status, "request_id": request_id}, status, [("Retry-After", retry_after)]
        deadline = time.monotonic() + resolved.api.timeout_seconds
        response, upstream_attempts, retry_count = request_with_retries(
            lambda timeout: forward_request(resolved, flask_request, request_id, gateway_policy, timeout),
            flask_request.method, resolved.api.max_retries if resolved.api.retry_enabled else 0,
            resolved.api.retry_backoff_ms, deadline,
        )
        body, status, headers = response_parts(response)
        if resolved.version is not None:
            schema = get_effective_schema(session, resolved.api.id, resolved.version.id, resolved.route.id, "response")
            if schema is not None:
                result = validate_payload(schema, response.json() if "application/json" in response.headers.get("Content-Type", "").lower() else None, response.headers.get("Content-Type"), bool(body))
                if not result.valid:
                    status = 502
                    breaker_result = record_failure(session, breaker)
                    health = record_result(session, resolved.api.id, resolved.route.id, False, (time.perf_counter() - started) * 1000, status)
                    _record_failure(session, resolved, api_key, flask_request, request_id, status, "response_validation", started)
                    emit_health_event(api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, state=health.state, latency_ms=health.average_latency_ms)
                    emit_gateway_error(request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, error_type="response_validation", status_code=status)
                    return {"error": "Upstream response validation failed", "status": status, "request_id": request_id}, status, []
        if resolved.version is not None:
            headers.append(("X-GateForge-API-Version", resolved.version.version))
            if resolved.version.status == "deprecated":
                headers.append(("Deprecation", "true"))
                if resolved.version.sunset_at is not None:
                    headers.append(("Sunset", resolved.version.sunset_at.strftime("%a, %d %b %Y %H:%M:%S GMT")))
        if resolved.version is not None:
            headers.append(("X-GateForge-API-Version", resolved.version.version))
            if resolved.version.status == "deprecated":
                headers.append(("Deprecation", "true"))
                if resolved.version.sunset_at is not None:
                    headers.append(("Sunset", resolved.version.sunset_at.strftime("%a, %d %b %Y %H:%M:%S GMT")))
        headers = apply_response_policy(headers, gateway_policy)
        headers.extend(_rate_headers(decision))
        logical_success = status not in {502, 503, 504} and status < 500
        breaker_result = record_success(session, breaker) if logical_success else record_failure(session, breaker)
        if breaker_result.changed:
            emit_circuit_event(api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, previous_state=breaker_result.previous_state, state=breaker_result.state)
        health = record_result(session, resolved.api.id, resolved.route.id, logical_success, (time.perf_counter() - started) * 1000, status)
        emit_health_event(api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, state=health.state, latency_ms=health.average_latency_ms)
        _record(
            session, request_id=request_id, api_id=resolved.api.id, api_version_id=resolved.version.id if resolved.version else None, route_id=resolved.route.id,
            api_key_id=api_key.id, user_id=api_key.user_id, method=flask_request.method,
            path=flask_request.path, upstream_url=build_upstream_url(resolved.api.base_url, resolved.target_path),
            status_code=status, duration_ms=int((time.perf_counter() - started) * 1000),
            request_size=flask_request.content_length or 0, response_size=len(body),
            rate_limit_allowed=True, rate_limit_remaining=decision.remaining,
            plan_id=policy.plan.id, limit_type=decision.policy_type,
            rate_limit_limit=decision.limit if not decision.quota else None,
            quota_limit=decision.limit if decision.quota else None,
            quota_remaining=decision.remaining if decision.quota else None, scope_authorized=True, policy_allowed=True, error_type=None,
            retry_count=retry_count, upstream_attempts=upstream_attempts, circuit_state=breaker_result.state if breaker_result.state else circuit.state,
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
            breaker_result = record_failure(session, breaker)
            health = record_result(session, resolved.api.id, resolved.route.id, False, (time.perf_counter() - started) * 1000, 504)
            emit_health_event(api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, state=health.state, latency_ms=health.average_latency_ms)
            emit_gateway_error(request_id=request_id, api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, error_type="upstream_timeout", status_code=504)
        return error_response("Upstream service timed out", 504, request_id)
    except (requests.RequestException, ValueError):
        status = 502
        if api_key is not None and resolved is not None:
            _record_failure(session, resolved, api_key, flask_request, request_id, 502, "upstream_connection", started)
            breaker_result = record_failure(session, breaker)
            health = record_result(session, resolved.api.id, resolved.route.id, False, (time.perf_counter() - started) * 1000, 502)
            emit_health_event(api_id=resolved.api.id, owner_id=api_key.user_id, route_id=resolved.route.id, state=health.state, latency_ms=health.average_latency_ms)
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
