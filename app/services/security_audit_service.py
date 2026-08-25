import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.security_audit_log import SecurityAuditLog

logger = logging.getLogger("gateforge.security")


def record_security_event(session: Session, event_type: str, *, user_id: UUID | None = None, api_key_id: UUID | None = None, api_id: UUID | None = None, ip_address: str | None = None, user_agent: str | None = None, request_id: str | None = None, metadata: dict | None = None, required: bool = False) -> None:
    try:
        session.add(SecurityAuditLog(user_id=user_id, api_key_id=api_key_id, api_id=api_id, event_type=event_type, ip_address=ip_address, user_agent=user_agent[:512] if user_agent else None, request_id=request_id, event_metadata=metadata or {}))
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Security audit event failed event=%s", event_type)
        if required:
            raise
