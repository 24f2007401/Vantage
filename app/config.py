import os
import sys
from datetime import timedelta


class Config:
    # ── Flask ──────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── SQLAlchemy connection pool ─────────────────────────────────
    # pool_pre_ping re-checks connections before use — critical for
    # cloud databases (Supabase PgBouncer) that drop idle connections.
    # pool_recycle keeps connections shorter than typical 5-min timeouts.
    SQLALCHEMY_POOL_PRE_PING = True
    SQLALCHEMY_POOL_RECYCLE = 280
    SQLALCHEMY_POOL_SIZE = 5
    SQLALCHEMY_MAX_OVERFLOW = 10
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    # ── Flask-JWT-Extended ─────────────────────────────────────────
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
    JWT_ALGORITHM = "HS256"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        seconds=int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRES", 900))
    )
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        seconds=int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRES", 604800))
    )
    # Enable blocklist checks on every @jwt_required route
    JWT_BLOCKLIST_ENABLED = True
    JWT_BLOCKLIST_TOKEN_CHECKS = ["access", "refresh"]

    # ── Flask-Login session cookies ────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    # ── JWT cookies ────────────────────────────────────────────────
    JWT_COOKIE_SAMESITE = "Lax"

    # ── Redis ──────────────────────────────────────────────────────
    REDIS_URL = os.environ.get("REDIS_URL")

    # ── Supabase Storage ────────────────────────────────────────
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
    )

    @staticmethod
    def validate():
        """
        Called at app startup. Exits immediately if required environment
        variables are missing rather than allowing silent misconfiguration.
        """
        required = ["SECRET_KEY", "JWT_SECRET_KEY", "DATABASE_URL"]
        missing = [v for v in required if not os.environ.get(v)]
        if missing:
            sys.exit(
                f"FATAL: Missing required environment variables: "
                f"{', '.join(missing)}\n"
                f"Copy .env.example → .env and fill in real values."
            )


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    JWT_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    JWT_BLOCKLIST_ENABLED = False
    SESSION_COOKIE_SECURE = False
    # Skip validation in tests — env vars won't be set
    @staticmethod
    def validate():
        pass


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
