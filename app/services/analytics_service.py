from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.api_key import APIKey
from app.models.api_route import APIRoute
from app.models.traffic_log import TrafficLog


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
    return [{"route_id": str(row[0]) if row[0] else None, "method": row[1], "path": row[2], "requests": int(row[3]), "errors": int(row[4]), "rate_limited": int(row[5]), "average_latency_ms": round(float(row[6]), 2)} for row in rows]


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
