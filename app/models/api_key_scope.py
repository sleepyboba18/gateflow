import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base

class APIKeyScope(Base):
    __tablename__ = "api_key_scopes"
    __table_args__ = (UniqueConstraint("api_key_id", "scope_id", name="uq_api_key_scopes_key_scope"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    api_key: Mapped["APIKey"] = relationship("APIKey", back_populates="scopes")
    scope: Mapped["Scope"] = relationship("Scope", back_populates="api_keys")
