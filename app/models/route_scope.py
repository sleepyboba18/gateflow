import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base

class RouteScope(Base):
    __tablename__ = "route_scopes"
    __table_args__ = (UniqueConstraint("route_id", "scope_id", name="uq_route_scopes_route_scope"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    route: Mapped["APIRoute"] = relationship("APIRoute", back_populates="scopes")
    scope: Mapped["Scope"] = relationship("Scope", back_populates="routes")
