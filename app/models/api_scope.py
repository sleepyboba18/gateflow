import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base

class APIScope(Base):
    __tablename__ = "api_scopes"
    __table_args__ = (UniqueConstraint("api_id", "scope_id", name="uq_api_scopes_api_scope"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("apis.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    api: Mapped["API"] = relationship("API", back_populates="scopes")
    scope: Mapped["Scope"] = relationship("Scope", back_populates="apis")
