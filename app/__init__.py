import logging
from typing import Any

from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

from config import Config

logger = logging.getLogger("gateforge")
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
    )

    CORS(app, origins=Config.CORS_ORIGINS)
    socketio.init_app(app, cors_allowed_origins=Config.CORS_ORIGINS)

    from app.routes.api_keys import api_keys_bp
    from app.routes.auth import auth_bp
    from app.routes.gateway import gateway_bp
    from app.routes.apis import apis_bp
    from app.routes.rate_limits import rate_limits_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(api_keys_bp, url_prefix="/api/v1/api-keys")
    app.register_blueprint(apis_bp, url_prefix="/api/v1/apis")
    app.register_blueprint(gateway_bp, url_prefix="/gateway")
    app.register_blueprint(rate_limits_bp, url_prefix="/api/v1/apis")

    @app.get("/health")
    def health() -> tuple[Any, int]:
        from app.database.connection import check_database_connection

        if not check_database_connection():
            return jsonify({"status": "error", "service": Config.APP_NAME, "database": "disconnected"}), 503
        return jsonify({"status": "ok", "service": Config.APP_NAME, "database": "connected"}), 200

    @app.errorhandler(404)
    def not_found(error: Exception) -> tuple[Any, int]:
        return jsonify({"error": "Not Found", "status": 404}), 404

    @app.errorhandler(405)
    def method_not_allowed(error: Exception) -> tuple[Any, int]:
        return jsonify({"error": "Method Not Allowed", "status": 405}), 405

    @app.errorhandler(500)
    def internal_server_error(error: Exception) -> tuple[Any, int]:
        logger.exception("Unhandled application error")
        return jsonify({"error": "Internal Server Error", "status": 500}), 500

    return app
