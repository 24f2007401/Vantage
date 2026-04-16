from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import redis as redis_lib

db = SQLAlchemy()
login_manager = LoginManager()
jwt = JWTManager()
cors = CORS()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per minute"])

# Redis client — populated by init_redis() inside create_app()
redis_store = None  # type: redis_lib.Redis | None


def init_redis(app):
    global redis_store
    try:
        redis_store = redis_lib.from_url(
            app.config.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        redis_store.ping()  # Verify connectivity
    except Exception:
        redis_store = None
    return redis_store
