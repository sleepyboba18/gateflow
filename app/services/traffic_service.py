import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.traffic_log import TrafficLog

logger = logging.getLogger("gateforge.traffic")


def record_traffic(session: Session, **values) -> None:
    try:
        session.add(TrafficLog(**values))
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Traffic record could not be persisted")


def traffic_summary(session: Session, api_id: UUID) -> dict:
    row = session.execute(
        select(
            func.count(TrafficLog.id),
            func.coalesce(func.sum(case((TrafficLog.status_code.between(200, 299), 1), else_=0)), 0),
            func.coalesce(func.sum(case((TrafficLog.status_code.between(400, 499), 1), else_=0)), 0),
            func.coalesce(func.sum(case((TrafficLog.status_code.between(500, 599), 1), else_=0)), 0),
            func.coalesce(func.sum(case((TrafficLog.rate_limit_allowed.is_(False), 1), else_=0)), 0),
            func.coalesce(func.avg(TrafficLog.duration_ms), 0),
        ).where(TrafficLog.api_id == api_id)
    ).one()
    return {
        "total_requests": int(row[0]), "successful_requests": int(row[1]),
        "client_errors": int(row[2]), "server_errors": int(row[3]),
        "rate_limited": int(row[4]), "average_duration_ms": round(float(row[5]), 2),
    }


def traffic_history(session: Session, api_id: UUID, page: int, per_page: int, filters: dict) -> tuple[list[TrafficLog], int]:
    conditions = [TrafficLog.api_id == api_id]
    for field in ("method", "route_id", "api_key_id"):
        if filters.get(field) is not None:
            conditions.append(getattr(TrafficLog, field) == filters[field])
    if filters.get("status_code") is not None:
        conditions.append(TrafficLog.status_code == filters["status_code"])
    if filters.get("from") is not None:
        conditions.append(TrafficLog.created_at >= filters["from"])
    if filters.get("to") is not None:
        conditions.append(TrafficLog.created_at <= filters["to"])
    total = session.scalar(select(func.count(TrafficLog.id)).where(*conditions)) or 0
    items = session.scalars(
        select(TrafficLog).where(*conditions).order_by(TrafficLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).all()
    return list(items), int(total)
