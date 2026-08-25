import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class QuotaCounter(Base):
    __tablename__ = "quota_counters"
    __table_args__ = (UniqueConstraint("plan_quota_id", "api_key_id", "period_start", name="uq_quota_counters_period"), Index("ix_quota_counters_current", "plan_quota_id", "api_key_id", "period_start"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_quota_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("plan_quotas.id", ondelete="CASCADE"), nullable=False, index=True)
    api_key_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    plan_quota: Mapped["PlanQuota"] = relationship("PlanQuota", back_populates="counters")
