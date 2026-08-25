import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        UniqueConstraint("rate_limit_id", "api_key_id", "window_start", name="uq_rate_limit_counters_window"),
        Index("ix_rate_limit_counters_current", "rate_limit_id", "api_key_id", "window_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rate_limit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rate_limits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    rate_limit: Mapped["RateLimit"] = relationship("RateLimit", back_populates="counters")
