import logging
from datetime import datetime, timezone

from app import socketio

logger = logging.getLogger("gateforge.sockets")


def _emit(event: str, payload: dict, rooms: list[str]) -> None:
    try:
        for room in rooms:
            socketio.emit(event, payload, to=room)
    except Exception:
        logger.exception("Socket event publishing failed event=%s", event)


def emit_traffic_event(*, request_id, api_id, owner_id, route_id, api_key_id, method, path, status_code, duration_ms):
    payload = {"request_id": request_id, "api_id": str(api_id), "route_id": str(route_id) if route_id else None, "method": method, "path": path, "status_code": status_code, "duration_ms": duration_ms, "created_at": datetime.now(timezone.utc).isoformat()}
    _emit("gateway:traffic", payload, [f"api:{api_id}", f"user:{owner_id}", f"api_key:{api_key_id}"])


def emit_rate_limit_event(*, request_id, api_id, owner_id, route_id, api_key_id, limit_type, limit, remaining, retry_after):
    payload = {"request_id": request_id, "api_id": str(api_id), "route_id": str(route_id), "api_key_id": str(api_key_id), "status_code": 429, "limit_type": limit_type, "limit": limit, "remaining": remaining, "retry_after": retry_after, "created_at": datetime.now(timezone.utc).isoformat()}
    _emit("gateway:rate_limit", payload, [f"api:{api_id}", f"user:{owner_id}", f"api_key:{api_key_id}"])


def emit_gateway_error(*, request_id, api_id=None, owner_id=None, route_id=None, error_type, status_code):
    payload = {"request_id": request_id, "api_id": str(api_id) if api_id else None, "route_id": str(route_id) if route_id else None, "error_type": error_type, "status_code": status_code, "created_at": datetime.now(timezone.utc).isoformat()}
    rooms = [room for room in [f"api:{api_id}" if api_id else None, f"user:{owner_id}" if owner_id else None] if room]
    _emit("gateway:error", payload, rooms)


def emit_health_event(*, api_id, owner_id, route_id, state, latency_ms):
    _emit("gateway:health", {"api_id": str(api_id), "route_id": str(route_id) if route_id else None, "state": state, "latency_ms": latency_ms, "created_at": datetime.now(timezone.utc).isoformat()}, [f"api:{api_id}", f"user:{owner_id}"])


def emit_circuit_event(*, api_id, owner_id, route_id, previous_state, state):
    _emit("gateway:circuit", {"api_id": str(api_id), "route_id": str(route_id) if route_id else None, "previous_state": previous_state, "state": state, "created_at": datetime.now(timezone.utc).isoformat()}, [f"api:{api_id}", f"user:{owner_id}"])


def emit_security_event(*, event_type, api_key_id=None, api_id=None, owner_id=None):
    payload = {"event_type": event_type, "api_key_id": str(api_key_id) if api_key_id else None, "api_id": str(api_id) if api_id else None, "created_at": datetime.now(timezone.utc).isoformat()}
    rooms = [room for room in [f"api:{api_id}" if api_id else None, f"user:{owner_id}" if owner_id else None, f"api_key:{api_key_id}" if api_key_id else None] if room]
    _emit("gateway:security", payload, rooms)
