import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (Index("uq_plans_active_default", "is_default", unique=True, postgresql_where=text("is_default IS TRUE AND is_active IS TRUE")),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    rate_limits: Mapped[list["PlanRateLimit"]] = relationship("PlanRateLimit", back_populates="plan", cascade="all, delete-orphan", passive_deletes=True)
    quotas: Mapped[list["PlanQuota"]] = relationship("PlanQuota", back_populates="plan", cascade="all, delete-orphan", passive_deletes=True)
    assignments: Mapped[list["APIKeyPlan"]] = relationship("APIKeyPlan", back_populates="plan", cascade="all, delete-orphan", passive_deletes=True)
