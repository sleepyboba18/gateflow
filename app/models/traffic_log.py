import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TrafficLog(Base):
    __tablename__ = "traffic_logs"
    __table_args__ = (
        Index("ix_traffic_logs_api_created", "api_id", "created_at"),
        Index("ix_traffic_logs_key_created", "api_key_id", "created_at"),
        Index("ix_traffic_logs_route_created", "route_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    api_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("apis.id", ondelete="SET NULL"), nullable=True, index=True)
    route_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_routes.id", ondelete="SET NULL"), nullable=True, index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    upstream_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("plans.id", ondelete="SET NULL"), nullable=True, index=True)
    limit_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rate_limit_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
