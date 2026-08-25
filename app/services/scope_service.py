from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_key_scope import APIKeyScope
from app.models.api_scope import APIScope
from app.models.route_scope import RouteScope
from app.models.scope import Scope


def valid_scope_name(name: str) -> bool:
    return isinstance(name, str) and len(name) <= 128 and bool(__import__("re").fullmatch(r"[a-z0-9]+(?::[a-z0-9_-]+)+", name))


def required_scope_ids(session: Session, api_id: UUID, route_id: UUID) -> set[UUID]:
    route_scopes = set(session.scalars(select(RouteScope.scope_id).where(RouteScope.route_id == route_id)))
    if route_scopes:
        return route_scopes
    return set(session.scalars(select(APIScope.scope_id).where(APIScope.api_id == api_id)))


def check_scope_access(session: Session, api_key_id: UUID, api_id: UUID, route_id: UUID) -> bool:
    required = required_scope_ids(session, api_id, route_id)
    if not required:
        return True
    granted = set(session.scalars(select(APIKeyScope.scope_id).where(APIKeyScope.api_key_id == api_key_id)))
    return required.issubset(granted)


def supported_by_api(session: Session, api_id: UUID, scope_id: UUID) -> bool:
    return session.scalar(select(APIScope.id).where(APIScope.api_id == api_id, APIScope.scope_id == scope_id)) is not None
