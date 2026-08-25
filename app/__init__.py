from typing import Any

from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

from config import Config
from app.observability.error_tracking import handle_exception
from app.observability.logger import configure_logging, log_event
from app.observability.request_logging import init_request_logging

logger = configure_logging(Config.LOG_LEVEL, Config.LOG_JSON)
socketio = SocketIO()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        APP_NAME=Config.APP_NAME,
        APP_ENV=Config.APP_ENV,
        DEBUG=Config.DEBUG,
        JWT_SECRET_KEY=Config.JWT_SECRET_KEY,
        JWT_ALGORITHM=Config.JWT_ALGORITHM,
        JWT_EXPIRATION_MINUTES=Config.JWT_EXPIRATION_MINUTES,
        ANALYTICS_MAX_DAYS=Config.ANALYTICS_MAX_DAYS,
        HEALTH_DB_TIMEOUT_SECONDS=Config.HEALTH_DB_TIMEOUT_SECONDS,
    )

    init_request_logging(app, logger)

    CORS(app, origins=Config.CORS_ALLOWED_ORIGINS)
    socketio.init_app(app, cors_allowed_origins=Config.CORS_ALLOWED_ORIGINS)

    from app.routes.api_keys import api_keys_bp
    from app.routes.auth import auth_bp
    from app.routes.gateway import gateway_bp
    from app.routes.apis import apis_bp
    from app.routes.rate_limits import rate_limits_bp
    from app.routes.plans import plans_bp
    from app.routes.policies import policies_bp
    from app.routes.reliability import reliability_bp
    from app.routes.versions import versions_bp
    from app.routes.schemas import schemas_bp
    from app.routes.security import security_bp
    from app.routes.analytics import analytics_bp, observability_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(api_keys_bp, url_prefix="/api/v1/api-keys")
    app.register_blueprint(apis_bp, url_prefix="/api/v1/apis")
    app.register_blueprint(gateway_bp, url_prefix="/gateway")
    app.register_blueprint(rate_limits_bp, url_prefix="/api/v1/apis")
    app.register_blueprint(plans_bp, url_prefix="/api/v1/plans")
    app.register_blueprint(policies_bp, url_prefix="/api/v1")
    app.register_blueprint(reliability_bp, url_prefix="/api/v1")
    app.register_blueprint(versions_bp, url_prefix="/api/v1")
    app.register_blueprint(schemas_bp, url_prefix="/api/v1")
    app.register_blueprint(security_bp, url_prefix="/api/v1")

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response
    app.register_blueprint(analytics_bp, url_prefix="/api/v1/apis")
    app.register_blueprint(observability_bp, url_prefix="/api/v1")

    from app.sockets import auth as socket_auth
    from app.sockets import events as socket_events
    from app.sockets import rooms as socket_rooms

    @app.get("/health")
    def health() -> tuple[Any, int]:
        from app.database.connection import check_database_connection

        if not check_database_connection():
            return jsonify({"status": "unhealthy", "service": Config.APP_NAME, "version": "1.0.0", "database": "unavailable"}), 503
        return jsonify({"status": "healthy", "service": Config.APP_NAME, "version": "1.0.0", "database": "healthy"}), 200

    @app.get("/health/live")
    def liveness() -> tuple[Any, int]:
        return jsonify({"status": "ok"}), 200

    @app.get("/health/ready")
    def readiness() -> tuple[Any, int]:
        from app.database.connection import check_database_connection

        if not check_database_connection():
            return jsonify({"status": "not_ready", "database": "unavailable"}), 503
        return jsonify({"status": "ready", "database": "available"}), 200

    log_event(logger, 20, "application.started", service=Config.APP_NAME, environment=Config.APP_ENV)

    def error_response(message, status):
        return jsonify({"error": message, "status": status, "request_id": getattr(__import__("flask").g, "request_id", None)}), status

    @app.errorhandler(404)
    def not_found(error: Exception) -> tuple[Any, int]:
        return error_response("Not Found", 404)

    @app.errorhandler(405)
    def method_not_allowed(error: Exception) -> tuple[Any, int]:
        return error_response("Method Not Allowed", 405)

    @app.errorhandler(500)
    def internal_server_error(error: Exception) -> tuple[Any, int]:
        handle_exception(logger, error)
        return error_response("Internal server error", 500)

    for status, message in ((400, "Bad Request"), (401, "Unauthorized"), (403, "Forbidden"), (413, "Request Entity Too Large"), (415, "Unsupported Media Type"), (429, "Too Many Requests"), (502, "Bad Gateway"), (503, "Service Unavailable"), (504, "Gateway Timeout")):
        app.register_error_handler(status, lambda error, message=message, status=status: error_response(message, status))

    return app
