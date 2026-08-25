import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base

class GatewayPolicy(Base):
    __tablename__ = "gateway_policies"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("apis.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_routes.id", ondelete="CASCADE"), nullable=True, index=True)
    require_api_key: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_scope: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_query_parameters: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_request_body: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_file_upload: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_request_size: Mapped[int] = mapped_column(Integer, default=1_048_576, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    api: Mapped["API"] = relationship("API", back_populates="gateway_policies")
    route: Mapped["APIRoute | None"] = relationship("APIRoute", back_populates="gateway_policies")
