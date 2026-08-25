from flask import Blueprint, jsonify, make_response, request

from app.services.gateway_service import handle_gateway_request, request_id_from


gateway_bp = Blueprint("gateway", __name__)


@gateway_bp.route("/<api_slug>/<path:request_path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def gateway(api_slug: str, request_path: str):
    request_id = request_id_from(request)
    result = handle_gateway_request(api_slug, request_path, request, request_id)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], dict):
        response = jsonify(result[0])
        response.status_code = result[1]
    else:
        body, status, headers = result
        response = make_response(body, status)
        for key, value in headers:
            response.headers[key] = value
    response.headers["X-Request-ID"] = request_id
    return response
