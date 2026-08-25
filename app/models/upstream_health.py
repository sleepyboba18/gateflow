import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Float, Index, Integer, String, Uuid, func, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class UpstreamHealth(Base):
    __tablename__ = "upstream_health"
    __table_args__ = (UniqueConstraint("api_id", "route_id", name="uq_upstream_health_api_route"), Index("uq_upstream_health_api_wide", "api_id", unique=True, postgresql_where=text("route_id IS NULL")))

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("apis.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_routes.id", ondelete="CASCADE"), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
