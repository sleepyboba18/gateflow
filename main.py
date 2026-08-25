import logging

from app import create_app, socketio
from app.database.connection import SessionLocal, initialize_database
from app.models import API, APIKey, APIKeyPlan, APIKeyScope, APIRoute, APIScope, APIVersion, CircuitBreaker, GatewayPolicy, GatewaySchema, HeaderPolicy, Plan, PlanQuota, PlanRateLimit, QuotaCounter, RateLimit, RateLimitCounter, RouteScope, Scope, TrafficLog, UpstreamHealth, User  # noqa: F401 - registers ORM models before table creation
from config import Config
from app.services.policy_service import ensure_default_plan


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gateforge")

app = create_app()


if __name__ == "__main__":
    initialize_database()
    if SessionLocal is not None:
        with SessionLocal() as session:
            ensure_default_plan(session)
    logger.info("%s started", Config.APP_NAME)
    socketio.run(app, host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
