import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class PlanRateLimit(Base):
    __tablename__ = "plan_rate_limits"
    __table_args__ = (UniqueConstraint("plan_id", "name", name="uq_plan_rate_limits_name"), Index("ix_plan_rate_limits_active", "plan_id", "is_active"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    plan: Mapped["Plan"] = relationship("Plan", back_populates="rate_limits")
    counters: Mapped[list["RateLimitCounter"]] = relationship("RateLimitCounter", back_populates="plan_rate_limit", cascade="all, delete-orphan", passive_deletes=True)
