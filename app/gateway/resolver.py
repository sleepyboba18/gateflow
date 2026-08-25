from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api import API
from app.models.api_route import APIRoute
from app.models.api_version import APIVersion


class GatewayResolutionError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ResolvedGatewayRequest:
    api: API
    version: APIVersion | None
    route: APIRoute
    target_path: str


def resolve_gateway_request(session: Session, api_slug: str, request_path: str, method: str, owner_id: UUID, version: str | None = None) -> ResolvedGatewayRequest:
    api = session.scalar(select(API).where(API.slug == api_slug))
    if api is None:
        raise GatewayResolutionError("Gateway API not found", 404)
    if api.owner_id != owner_id:
        raise GatewayResolutionError("API key does not have access to this API", 403)
    if not api.is_active:
        raise GatewayResolutionError("Gateway API is inactive", 403)

    selected_version = None
    if version is not None:
        selected_version = session.scalar(select(APIVersion).where(APIVersion.api_id == api.id, APIVersion.version == version))
        if selected_version is None or not selected_version.is_active or selected_version.status == "disabled":
            raise GatewayResolutionError("API version not found", 404)
        from datetime import datetime, timezone
        if selected_version.sunset_at is not None and selected_version.sunset_at <= datetime.now(timezone.utc):
            raise GatewayResolutionError("API version has been sunset", 410)

    normalized_path = "/" + request_path.lstrip("/")
    route_query = select(APIRoute).where(APIRoute.api_id == api.id, APIRoute.path == normalized_path)
    if selected_version is not None:
        route_query = route_query.where(APIRoute.version_id == selected_version.id)
    path_routes = session.scalars(route_query).all()
    if not path_routes:
        raise GatewayResolutionError("Gateway route not found", 404)
    route = next((item for item in path_routes if item.method == method.upper()), None)
    if route is None:
        raise GatewayResolutionError("Method not allowed", 405)
    if not route.is_active:
        raise GatewayResolutionError("Gateway route is inactive", 403)
    return ResolvedGatewayRequest(api=api, version=selected_version, route=route, target_path=route.target_path or normalized_path)
