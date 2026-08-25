import logging

from app import create_app, socketio
from app.database.connection import initialize_database
from app.models import APIKey, User  # noqa: F401 - registers ORM models before table creation
from config import Config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gateforge")

app = create_app()


if __name__ == "__main__":
    initialize_database()
    logger.info("%s started", Config.APP_NAME)
    socketio.run(app, host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
