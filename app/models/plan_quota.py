import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class PlanQuota(Base):
    __tablename__ = "plan_quotas"
    __table_args__ = (UniqueConstraint("plan_id", "period", name="uq_plan_quotas_period"), Index("ix_plan_quotas_active", "plan_id", "period", "is_active"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    plan: Mapped["Plan"] = relationship("Plan", back_populates="quotas")
    counters: Mapped[list["QuotaCounter"]] = relationship("QuotaCounter", back_populates="plan_quota", cascade="all, delete-orphan", passive_deletes=True)
