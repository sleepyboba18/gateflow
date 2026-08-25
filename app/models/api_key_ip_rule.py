import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base

class APIKeyIPRule(Base):
    __tablename__ = "api_key_ip_rules"
    __table_args__ = (UniqueConstraint("api_key_id", "cidr", "rule_type", name="uq_api_key_ip_rules"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True)
    cidr: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    api_key: Mapped["APIKey"] = relationship("APIKey", back_populates="ip_rules")
