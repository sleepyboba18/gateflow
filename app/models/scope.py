import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Scope(Base):
    __tablename__ = "scopes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    api_keys: Mapped[list["APIKeyScope"]] = relationship("APIKeyScope", back_populates="scope", cascade="all, delete-orphan")
    apis: Mapped[list["APIScope"]] = relationship("APIScope", back_populates="scope", cascade="all, delete-orphan")
    routes: Mapped[list["RouteScope"]] = relationship("RouteScope", back_populates="scope", cascade="all, delete-orphan")
