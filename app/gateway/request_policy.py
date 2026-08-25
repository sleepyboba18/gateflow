from flask import Request

FORBIDDEN_HEADERS = {"authorization", "x-api-key", "cookie", "host", "content-length", "connection", "transfer-encoding", "set-cookie"}


class RequestPolicyViolation(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate_header(name: str, value: str | None = None) -> bool:
    return bool(name) and not any(ord(char) < 32 for char in name) and (value is None or "\r" not in value and "\n" not in value)


def apply_request_policy(headers: dict[str, str], request: Request, policy, request_id: str, api_slug: str, route_path: str) -> tuple[dict[str, str] | None, str | None]:
    if request.query_string and not policy.allow_query_parameters:
        return None, "Query parameters are not allowed"
    if request.content_length and request.content_length > policy.max_request_size:
        return None, "Request body exceeds configured limit"
    if request.get_data(cache=True) and not policy.allow_request_body:
        return None, "Request body is not allowed"
    if request.mimetype == "multipart/form-data" and not policy.allow_file_upload:
        return None, "File uploads are not allowed"
    headers = dict(headers)
    headers["X-GateForge-Request-ID"] = request_id
    headers["X-GateForge-API"] = api_slug
    headers["X-GateForge-Route"] = route_path.strip("/")
    for item in policy.request_headers:
        name = item.header_name.lower()
        if name in FORBIDDEN_HEADERS or not validate_header(item.header_name, item.header_value):
            continue
        if item.action in {"add", "replace"} and item.header_value is not None:
            headers[item.header_name] = item.header_value
        elif item.action == "remove":
            headers.pop(item.header_name, None)
    return headers, None
