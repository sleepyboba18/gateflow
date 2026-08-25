from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.api_key import APIKey
from app.models.api_route import APIRoute
from app.models.traffic_log import TrafficLog
from app.models.api_version import APIVersion
from app.models.security_audit_log import SecurityAuditLog


def parse_period(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc) - timedelta(hours=24)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def period_filters(api_id: UUID, start: datetime, end: datetime):
    return TrafficLog.api_id == api_id, TrafficLog.created_at >= start, TrafficLog.created_at <= end


def overview(session: Session, api_id: UUID, start: datetime, end: datetime) -> dict:
    row = session.execute(select(
        func.count(TrafficLog.id),
        func.coalesce(func.sum(case((TrafficLog.status_code.between(200, 299), 1), else_=0)), 0),
        func.coalesce(func.sum(case((TrafficLog.status_code.between(400, 499), 1), else_=0)), 0),
        func.coalesce(func.sum(case((TrafficLog.status_code.between(500, 599), 1), else_=0)), 0),
        func.coalesce(func.sum(case((TrafficLog.rate_limit_allowed.is_(False), 1), else_=0)), 0),
        func.coalesce(func.avg(TrafficLog.duration_ms), 0),
    ).where(*period_filters(api_id, start, end))).one()
    total = int(row[0])
    errors = int(row[2]) + int(row[3])
    return {"total_requests": total, "successful_requests": int(row[1]), "client_errors": int(row[2]), "server_errors": int(row[3]), "rate_limited": int(row[4]), "average_latency_ms": round(float(row[5]), 2), "error_rate": round(errors / total, 4) if total else 0.0}


def timeseries(session: Session, api_id: UUID, start: datetime, end: datetime, granularity: str):
    if granularity not in {"minute", "hour", "day"}:
        raise ValueError("Invalid granularity")
    bucket = func.date_trunc(granularity, TrafficLog.created_at).label("timestamp")
    rows = session.execute(select(bucket, func.count(TrafficLog.id), func.coalesce(func.sum(case((TrafficLog.status_code >= 400, 1), else_=0)), 0), func.coalesce(func.sum(case((TrafficLog.rate_limit_allowed.is_(False), 1), else_=0)), 0), func.coalesce(func.avg(TrafficLog.duration_ms), 0)).where(*period_filters(api_id, start, end)).group_by(bucket).order_by(bucket)).all()
    return [{"timestamp": row[0].astimezone(timezone.utc).isoformat(), "requests": int(row[1]), "errors": int(row[2]), "rate_limited": int(row[3]), "average_latency_ms": round(float(row[4]), 2)} for row in rows]


def route_stats(session: Session, api_id: UUID, start: datetime, end: datetime):
    rows = session.execute(select(TrafficLog.route_id, TrafficLog.method, TrafficLog.path, func.count(TrafficLog.id), func.coalesce(func.sum(case((TrafficLog.status_code >= 400, 1), else_=0)), 0), func.coalesce(func.sum(case((TrafficLog.rate_limit_allowed.is_(False), 1), else_=0)), 0), func.coalesce(func.avg(TrafficLog.duration_ms), 0)).where(*period_filters(api_id, start, end)).group_by(TrafficLog.route_id, TrafficLog.method, TrafficLog.path).order_by(func.count(TrafficLog.id).desc())).all()
    return [{"route_id": str(row[0]) if row[0] else None, "method": row[1], "path": row[2], "requests": int(row[3]), "errors": int(row[4]), "success_rate": round((int(row[3]) - int(row[4])) / int(row[3]) * 100, 2) if row[3] else 0.0, "rate_limited": int(row[5]), "average_latency_ms": round(float(row[6]), 2)} for row in rows]


def key_stats(session: Session, api_id: UUID, start: datetime, end: datetime):
    rows = session.execute(select(TrafficLog.api_key_id, APIKey.key_prefix, func.count(TrafficLog.id), func.coalesce(func.sum(case((TrafficLog.status_code >= 400, 1), else_=0)), 0), func.coalesce(func.sum(case((TrafficLog.rate_limit_allowed.is_(False), 1), else_=0)), 0), func.coalesce(func.avg(TrafficLog.duration_ms), 0)).join(APIKey, APIKey.id == TrafficLog.api_key_id).where(*period_filters(api_id, start, end)).group_by(TrafficLog.api_key_id, APIKey.key_prefix).order_by(func.count(TrafficLog.id).desc())).all()
    return [{"api_key_id": str(row[0]), "key_prefix": row[1], "requests": int(row[2]), "errors": int(row[3]), "rate_limited": int(row[4]), "average_latency_ms": round(float(row[5]), 2)} for row in rows]


def status_stats(session: Session, api_id: UUID, start: datetime, end: datetime):
    rows = session.execute(select(TrafficLog.status_code, func.count(TrafficLog.id)).where(*period_filters(api_id, start, end)).group_by(TrafficLog.status_code).order_by(TrafficLog.status_code)).all()
    return [{"status_code": int(row[0]), "count": int(row[1])} for row in rows]


def latency_stats(session: Session, api_id: UUID, start: datetime, end: datetime):
    row = session.execute(select(func.coalesce(func.avg(TrafficLog.duration_ms), 0), func.coalesce(func.min(TrafficLog.duration_ms), 0), func.coalesce(func.max(TrafficLog.duration_ms), 0)).where(*period_filters(api_id, start, end))).one()
    return {"average": round(float(row[0]), 2), "minimum": int(row[1]), "maximum": int(row[2])}


def error_stats(session: Session, api_id: UUID, start: datetime, end: datetime):
    rows = session.execute(select(TrafficLog.error_type, func.count(TrafficLog.id)).where(*period_filters(api_id, start, end), TrafficLog.status_code >= 400, TrafficLog.error_type.is_not(None)).group_by(TrafficLog.error_type).order_by(func.count(TrafficLog.id).desc())).all()
    return [{"error_type": row[0], "count": int(row[1])} for row in rows]


def snapshot(session: Session, api_id: UUID) -> dict:
    end = datetime.now(timezone.utc)
    data = overview(session, api_id, end - timedelta(hours=1), end)
    return {"api_id": str(api_id), "requests_last_hour": data["total_requests"], "errors_last_hour": data["client_errors"] + data["server_errors"], "rate_limited_last_hour": data["rate_limited"], "average_latency_ms": data["average_latency_ms"]}


def reliability(session: Session, api_id: UUID, start: datetime, end: datetime) -> dict:
    eligible = TrafficLog.status_code.not_in([401, 403, 429])
    row = session.execute(select(
        func.count(TrafficLog.id).filter(eligible),
        func.count(TrafficLog.id).filter(TrafficLog.status_code.between(200, 299)),
        func.count(TrafficLog.id).filter(TrafficLog.status_code.in_([502, 503, 504])),
        func.count(TrafficLog.id).filter(TrafficLog.error_type == "upstream_timeout"),
        func.count(TrafficLog.id).filter(TrafficLog.error_type == "upstream_connection"),
        func.coalesce(func.sum(TrafficLog.retry_count), 0),
        func.coalesce(func.sum(TrafficLog.upstream_attempts), 0),
        func.coalesce(func.avg(TrafficLog.duration_ms), 0),
    ).where(*period_filters(api_id, start, end))).one()
    eligible_count, successful, failures, timeouts, connections, retries, attempts, average = map(int, row)
    return {"availability_percent": round((successful / eligible_count * 100) if eligible_count else 100, 2), "success_rate": round((successful / eligible_count * 100) if eligible_count else 0, 2), "failure_rate": round((failures / eligible_count * 100) if eligible_count else 0, 2), "average_latency_ms": round(float(row[7]), 2), "timeouts": timeouts, "connection_failures": connections, "retries": retries, "upstream_attempts": attempts}


def reliability_timeseries(session: Session, api_id: UUID, start: datetime, end: datetime, granularity: str):
    if granularity not in {"minute", "hour", "day"}:
        raise ValueError("Invalid granularity")
    bucket = func.date_trunc(granularity, TrafficLog.created_at).label("timestamp")
    rows = session.execute(select(bucket, func.count(TrafficLog.id), func.coalesce(func.sum(case((TrafficLog.status_code.in_([502, 503, 504]), 1), else_=0)), 0), func.coalesce(func.sum(case((TrafficLog.error_type == "upstream_timeout", 1), else_=0)), 0), func.coalesce(func.avg(TrafficLog.duration_ms), 0)).where(*period_filters(api_id, start, end)).group_by(bucket).order_by(bucket)).all()
    return [{"timestamp": row[0].astimezone(timezone.utc).isoformat(), "requests": int(row[1]), "failures": int(row[2]), "timeouts": int(row[3]), "average_latency_ms": round(float(row[4]), 2)} for row in rows]


def version_stats(session: Session, api_id: UUID, start: datetime, end: datetime):
    rows = session.execute(select(APIVersion.version, func.count(TrafficLog.id), func.coalesce(func.sum(case((TrafficLog.status_code >= 400, 1), else_=0)), 0), func.coalesce(func.sum(case((TrafficLog.rate_limit_allowed.is_(False), 1), else_=0)), 0), func.coalesce(func.avg(TrafficLog.duration_ms), 0)).join(TrafficLog, TrafficLog.api_version_id == APIVersion.id).where(*period_filters(api_id, start, end)).group_by(APIVersion.version).order_by(APIVersion.version)).all()
    return [{"version": row[0], "requests": int(row[1]), "errors": int(row[2]), "success_rate": round((int(row[1]) - int(row[2])) / int(row[1]) * 100, 2) if row[1] else 0.0, "rate_limited": int(row[3]), "average_latency_ms": round(float(row[4]), 2)} for row in rows]


def _traffic_metrics(session: Session, start: datetime, end: datetime, api_id: UUID | None = None, api_key_id: UUID | None = None):
    filters = (TrafficLog.created_at >= start, TrafficLog.created_at <= end)
    if api_id is not None:
        filters += (TrafficLog.api_id == api_id,)
    if api_key_id is not None:
        filters += (TrafficLog.api_key_id == api_key_id,)
    row = session.execute(select(
        func.count(TrafficLog.id),
        func.count(TrafficLog.id).filter(TrafficLog.status_code.between(200, 299)),
        func.count(TrafficLog.id).filter(TrafficLog.status_code >= 400),
        func.count(TrafficLog.id).filter(TrafficLog.rate_limit_allowed.is_(False)),
        func.count(TrafficLog.id).filter(TrafficLog.error_type == "quota"),
        func.count(TrafficLog.id).filter(TrafficLog.error_type.in_(["upstream_connection", "upstream_error"])),
        func.count(TrafficLog.id).filter(TrafficLog.error_type == "upstream_timeout"),
        func.coalesce(func.sum(TrafficLog.retry_count), 0),
        func.coalesce(func.avg(TrafficLog.duration_ms), 0),
        func.coalesce(func.min(TrafficLog.duration_ms), 0),
        func.coalesce(func.max(TrafficLog.duration_ms), 0),
    ).where(*filters)).one()
    total = int(row[0])
    return {"requests": total, "successful_requests": int(row[1]), "failed_requests": int(row[2]), "rate_limited": int(row[3]), "quota_rejected": int(row[4]), "upstream_failures": int(row[5]), "timeouts": int(row[6]), "retries": int(row[7]), "average_latency_ms": round(float(row[8]), 2), "minimum_latency_ms": int(row[9]), "maximum_latency_ms": int(row[10]), "success_rate": round(int(row[1]) / total * 100, 2) if total else 0.0}


def get_request_metrics(session: Session, start: datetime, end: datetime, api_id=None, api_key_id=None):
    return _traffic_metrics(session, start, end, api_id, api_key_id)


def get_latency_metrics(session: Session, start: datetime, end: datetime, api_id=None, api_key_id=None):
    data = _traffic_metrics(session, start, end, api_id, api_key_id)
    filters = (TrafficLog.created_at >= start, TrafficLog.created_at <= end)
    if api_id is not None: filters += (TrafficLog.api_id == api_id,)
    if api_key_id is not None: filters += (TrafficLog.api_key_id == api_key_id,)
    row = session.execute(select(
        func.percentile_cont(0.5).within_group(TrafficLog.duration_ms),
        func.percentile_cont(0.95).within_group(TrafficLog.duration_ms),
        func.percentile_cont(0.99).within_group(TrafficLog.duration_ms),
    ).where(*filters)).one()
    return {"average_ms": data["average_latency_ms"], "p50_ms": round(float(row[0] or 0), 2), "p95_ms": round(float(row[1] or 0), 2), "p99_ms": round(float(row[2] or 0), 2), "minimum_ms": data["minimum_latency_ms"], "maximum_ms": data["maximum_latency_ms"]}


def get_error_metrics(session: Session, start: datetime, end: datetime, api_id=None):
    filters = (TrafficLog.created_at >= start, TrafficLog.created_at <= end, TrafficLog.status_code >= 400)
    if api_id is not None: filters += (TrafficLog.api_id == api_id,)
    rows = session.execute(select(TrafficLog.error_type, func.count(TrafficLog.id)).where(*filters).group_by(TrafficLog.error_type).order_by(func.count(TrafficLog.id).desc())).all()
    return [{"error_type": row[0] or "unknown", "count": int(row[1])} for row in rows]


def get_upstream_metrics(session: Session, start: datetime, end: datetime, api_id=None):
    data = _traffic_metrics(session, start, end, api_id)
    return {"failures": data["upstream_failures"], "timeouts": data["timeouts"], "retries": data["retries"]}


def get_security_metrics(session: Session, start: datetime, end: datetime):
    rows = session.execute(select(SecurityAuditLog.event_type, func.count(SecurityAuditLog.id)).where(SecurityAuditLog.created_at >= start, SecurityAuditLog.created_at <= end).group_by(SecurityAuditLog.event_type)).all()
    counts = dict(rows)
    return {"authentication_failures": counts.get("api_key_authentication_failed", 0), "authorization_failures": counts.get("authorization_failed", 0) + counts.get("api_key_scope_denied", 0), "ip_denials": counts.get("api_key_ip_denied", 0), "origin_denials": counts.get("api_key_origin_denied", 0), "revocations": counts.get("api_key_revoked", 0), "rotations": counts.get("api_key_rotated", 0), "suspensions": counts.get("api_key_suspended", 0)}
