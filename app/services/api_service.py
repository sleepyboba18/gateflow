import ipaddress
import socket
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.api import API
from app.models.api_route import APIRoute

SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 60


def validate_base_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        return all(_is_public_address(ipaddress.ip_address(address[4][0])) for address in addresses)
    except (OSError, ValueError, UnicodeError):
        return False


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified)


def validate_route_path(value: str | None, allow_none: bool = False) -> bool:
    return value is None and allow_none or isinstance(value, str) and value.startswith("/") and not value.startswith("//") and "?" not in value and "#" not in value


def get_owned_api(session: Session, user_id: UUID, api_id: UUID, is_admin: bool = False) -> API | None:
    query = select(API).options(selectinload(API.routes)).where(API.id == api_id)
    if not is_admin:
        query = query.where(API.owner_id == user_id)
    return session.scalar(query)


def get_api_by_slug(session: Session, slug: str) -> API | None:
    return session.scalar(select(API).where(API.slug == slug))


def get_route(session: Session, api_id: UUID, route_id: UUID) -> APIRoute | None:
    return session.scalar(select(APIRoute).where(APIRoute.id == route_id, APIRoute.api_id == api_id))


def create_api(session: Session, **values) -> API:
    api = API(**values)
    session.add(api)
    return api


def create_route(session: Session, **values) -> APIRoute:
    route = APIRoute(**values)
    session.add(route)
    return route


def commit_or_raise_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ValueError("A resource with the same unique identifier already exists") from error
