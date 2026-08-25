from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gateway_policy import GatewayPolicy
from app.models.header_policy import HeaderPolicy


@dataclass(frozen=True)
class EffectiveGatewayPolicy:
    require_api_key: bool = True
    require_scope: bool = True
    allow_query_parameters: bool = True
    allow_request_body: bool = True
    allow_file_upload: bool = False
    max_request_size: int = 1_048_576
    request_headers: tuple[HeaderPolicy, ...] = ()
    response_headers: tuple[HeaderPolicy, ...] = ()


def resolve_gateway_policy(session: Session, api_id: UUID, route_id: UUID) -> EffectiveGatewayPolicy:
    policies = session.scalars(select(GatewayPolicy).where(GatewayPolicy.api_id == api_id, GatewayPolicy.is_active.is_(True), (GatewayPolicy.route_id == route_id) | GatewayPolicy.route_id.is_(None)).order_by(GatewayPolicy.route_id.is_(None))).all()
    selected = policies[0] if policies else None
    headers = session.scalars(select(HeaderPolicy).where(HeaderPolicy.api_id == api_id, HeaderPolicy.is_active.is_(True), (HeaderPolicy.route_id == route_id) | HeaderPolicy.route_id.is_(None)).order_by(HeaderPolicy.route_id.is_(None))).all()
    request_headers = tuple(item for item in headers if item.direction == "request")
    response_headers = tuple(item for item in headers if item.direction == "response")
    if selected is None:
        return EffectiveGatewayPolicy(request_headers=request_headers, response_headers=response_headers)
    return EffectiveGatewayPolicy(
        require_api_key=selected.require_api_key, require_scope=selected.require_scope,
        allow_query_parameters=selected.allow_query_parameters, allow_request_body=selected.allow_request_body,
        allow_file_upload=selected.allow_file_upload, max_request_size=selected.max_request_size,
        request_headers=request_headers, response_headers=response_headers,
    )
