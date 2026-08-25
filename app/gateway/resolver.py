from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api import API
from app.models.api_route import APIRoute


class GatewayResolutionError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ResolvedGatewayRequest:
    api: API
    route: APIRoute
    target_path: str


def resolve_gateway_request(session: Session, api_slug: str, request_path: str, method: str, owner_id: UUID) -> ResolvedGatewayRequest:
    api = session.scalar(select(API).where(API.slug == api_slug))
    if api is None:
        raise GatewayResolutionError("Gateway API not found", 404)
    if api.owner_id != owner_id:
        raise GatewayResolutionError("API key does not have access to this API", 403)
    if not api.is_active:
        raise GatewayResolutionError("Gateway API is inactive", 403)

    normalized_path = "/" + request_path.lstrip("/")
    path_routes = session.scalars(select(APIRoute).where(APIRoute.api_id == api.id, APIRoute.path == normalized_path)).all()
    if not path_routes:
        raise GatewayResolutionError("Gateway route not found", 404)
    route = next((item for item in path_routes if item.method == method.upper()), None)
    if route is None:
        raise GatewayResolutionError("Method not allowed", 405)
    if not route.is_active:
        raise GatewayResolutionError("Gateway route is inactive", 403)
    return ResolvedGatewayRequest(api=api, route=route, target_path=route.target_path or normalized_path)
