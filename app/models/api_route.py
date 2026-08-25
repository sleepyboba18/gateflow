import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class APIRoute(Base):
    __tablename__ = "api_routes"
    __table_args__ = (
        UniqueConstraint("api_id", "method", "path", name="uq_api_routes_api_method_path"),
        Index("ix_api_routes_api_path_method", "api_id", "path", "method"),
        Index("ix_api_routes_is_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("apis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    target_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    api: Mapped["API"] = relationship("API", back_populates="routes")
    rate_limits: Mapped[list["RateLimit"]] = relationship(
        "RateLimit", back_populates="route", cascade="all, delete-orphan", passive_deletes=True
    )
