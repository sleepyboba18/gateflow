import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class API(Base):
    __tablename__ = "apis"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    upstream_auth_type: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    upstream_auth_value: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    upstream_auth_header: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    owner: Mapped["User"] = relationship("User", back_populates="apis")
    routes: Mapped[list["APIRoute"]] = relationship(
        "APIRoute", back_populates="api", cascade="all, delete-orphan", passive_deletes=True
    )
    rate_limits: Mapped[list["RateLimit"]] = relationship(
        "RateLimit", back_populates="api", cascade="all, delete-orphan", passive_deletes=True
    )
    scopes: Mapped[list["APIScope"]] = relationship("APIScope", back_populates="api", cascade="all, delete-orphan", passive_deletes=True)
    gateway_policies: Mapped[list["GatewayPolicy"]] = relationship("GatewayPolicy", back_populates="api", cascade="all, delete-orphan", passive_deletes=True)
