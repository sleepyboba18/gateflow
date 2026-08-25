import logging
from urllib.parse import urljoin, urlparse

import requests
from flask import Request

from app.gateway.resolver import ResolvedGatewayRequest
from app.services.api_service import validate_base_url
from app.gateway.request_policy import apply_request_policy

logger = logging.getLogger("gateforge.gateway")
SAFE_REQUEST_HEADERS = {"content-type", "accept", "user-agent", "x-request-id"}
SAFE_RESPONSE_HEADERS = {"content-type", "cache-control", "content-encoding", "etag", "expires", "last-modified", "location", "vary"}


def build_upstream_url(base_url: str, target_path: str) -> str:
    if not validate_base_url(base_url):
        raise ValueError("Unsafe upstream URL")
    parsed = urlparse(base_url)
    return urljoin(f"{parsed.scheme}://{parsed.netloc}/", target_path.lstrip("/"))


def forward_request(resolved: ResolvedGatewayRequest, flask_request: Request, request_id: str, policy=None) -> requests.Response:
    url = build_upstream_url(resolved.api.base_url, resolved.target_path)
    headers = {
        key: value for key, value in flask_request.headers.items() if key.lower() in SAFE_REQUEST_HEADERS and key.lower() != "content-length"
    }
    headers["X-Request-ID"] = request_id
    if policy is not None:
        headers, error = apply_request_policy(headers, flask_request, policy, request_id, resolved.api.slug, resolved.route.path)
        if error:
            raise ValueError(error)
    if resolved.api.upstream_auth_type == "bearer" and resolved.api.upstream_auth_value:
        headers["Authorization"] = f"Bearer {resolved.api.upstream_auth_value}"
    elif resolved.api.upstream_auth_type == "api_key" and resolved.api.upstream_auth_value:
        headers[resolved.api.upstream_auth_header or "X-Upstream-API-Key"] = resolved.api.upstream_auth_value
    elif resolved.api.upstream_auth_type == "basic" and resolved.api.upstream_auth_value:
        headers["Authorization"] = f"Basic {resolved.api.upstream_auth_value}"
    return requests.request(
        method=flask_request.method,
        url=url,
        params=list(flask_request.args.items(multi=True)),
        data=flask_request.get_data(),
        headers=headers,
        timeout=resolved.api.timeout_seconds,
        allow_redirects=False,
    )


def response_parts(response: requests.Response) -> tuple[bytes, int, list[tuple[str, str]]]:
    headers = [(key, value) for key, value in response.headers.items() if key.lower() in SAFE_RESPONSE_HEADERS]
    return response.content, response.status_code, headers
