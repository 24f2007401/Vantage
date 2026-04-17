"""
Vantage — Application Factory
Creates and configures the Flask application instance.
"""
import json
import logging
import os
import sys
import time
from flask import Flask, g, request
from app.config import config_map
from app.extensions import db, login_manager, jwt, cors, init_redis, limiter, migrate


# ── Structured JSON logging ─────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line — parseable by log aggregators."""
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
            "module":    record.module,
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


def _configure_logging(app: Flask) -> None:
    """Replace the default handler with a JSON formatter in non-debug mode."""
    if not app.debug:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        app.logger.handlers = [handler]
        app.logger.setLevel(logging.INFO)
        # Silence noisy library loggers in production
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ── Application factory ─────────────────────────────────────────────────────

def create_app(config_name=None):
    """Create and configure the Flask application."""

    # ── Load Configuration ──────────────────────────────────────
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    cfg = config_map[config_name]

    # Validate required env vars before doing anything else.
    # TestingConfig overrides this to a no-op.
    cfg.validate()

    # ── Sentry error monitoring (optional) ──────────────────────
    sentry_dsn = os.environ.get("SENTRY_DSN")
    if sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.flask import FlaskIntegration
            sentry_sdk.init(
                dsn=sentry_dsn,
                integrations=[FlaskIntegration()],
                traces_sample_rate=0.1,
                environment=config_name,
                send_default_pii=False,   # never send passwords / tokens
            )
        except ImportError:
            pass  # sentry-sdk not installed — silently skip

    app = Flask(__name__, static_url_path='/static')
    app.config.from_object(cfg)
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

    # Render (and most PaaS) terminate TLS at the edge and forward plain
    # HTTP to gunicorn. Without ProxyFix, Flask sees http:// and refuses
    # to set Secure cookies — tokens are dropped on every response.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # ── Configure structured logging ────────────────────────────
    _configure_logging(app)

    # ── File upload limit ───────────────────────────────────────
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

    # ── Rate limiter storage ─────────────────────────────────────
    # Use Redis only when a genuinely remote URL is configured.
    # "localhost" / "127.0.0.1" / "::1" indicate a local instance that
    # won't exist in multi-worker cloud deployment — fall back to memory.
    redis_url = app.config.get("REDIS_URL", "")
    _local_indicators = ["localhost", "127.0.0.1", "::1"]
    _is_remote_redis = redis_url and not any(
        indicator in redis_url for indicator in _local_indicators
    )
    if _is_remote_redis:
        app.config["RATELIMIT_STORAGE_URI"] = redis_url
    else:
        app.config["RATELIMIT_STORAGE_URI"] = "memory://"
        app.logger.warning(
            "Rate limiter using in-memory storage. "
            "Set a remote REDIS_URL for production rate limiting."
        )
    app.config.setdefault("RATELIMIT_SWALLOW_ERRORS", True)

    # ── Initialise Extensions ───────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    jwt.init_app(app)

    # CORS: restrict to the configured frontend origin(s) — never wildcard
    allowed_origins = [
        o.strip()
        for o in os.environ.get(
            "FRONTEND_ORIGIN", "http://localhost:5000"
        ).split(",")
        if o.strip()
    ]
    cors.init_app(app, resources={
        r"/api/*": {
            "origins":             allowed_origins,
            "supports_credentials": True,
            "allow_headers":       ["Content-Type", "Authorization"],
            "methods":             ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        }
    })

    init_redis(app)
    limiter.init_app(app)

    # ── Global Error Handlers ───────────────────────────────────
    from app.errors import register_error_handlers
    register_error_handlers(app)

    # ── Request Logging Middleware ──────────────────────────────
    @app.before_request
    def _log_request():
        g._req_start = time.monotonic()
        try:
            from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
            verify_jwt_in_request(optional=True)
            identity = get_jwt_identity() or "anon"
        except Exception:
            identity = "anon"
        app.logger.info(
            "[REQ] %s %s — IP: %s — User: %s — ReqID: %s",
            request.method, request.path,
            request.remote_addr, identity,
            getattr(g, "request_id", "-"),
        )

    @app.after_request
    def _log_response(response):
        duration_ms = int(
            (time.monotonic() - getattr(g, "_req_start", time.monotonic())) * 1000
        )
        status = response.status_code
        log = app.logger.error if status >= 500 else (
            app.logger.warning if status >= 400 else app.logger.info
        )
        log(
            "[RES] %s %s — %d — %dms — ReqID: %s",
            request.method, request.path,
            status, duration_ms,
            getattr(g, "request_id", "-"),
        )
        return response

    # ── Security Headers ────────────────────────────────────────
    @app.after_request
    def _security_headers(response):
        if request.path.startswith("/static"):
            return response
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https: data:; "
            "style-src-elem 'self' 'unsafe-inline' https: data:; "
            "font-src 'self' https: data:; "
            "script-src 'self' 'unsafe-inline' https: data:; "
            "script-src-elem 'self' 'unsafe-inline' https: data:; "
            "img-src 'self' data: https: blob:; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none';"
        )
        if not app.config.get("DEBUG"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    # ── Configure Flask-Login ───────────────────────────────────
    login_manager.login_view = "auth.login"
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def load_user(user_id: str):
        from app.models.user import User
        user = db.session.get(User, user_id)
        return user if (user and user.is_active) else None

    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import request, jsonify, redirect, url_for
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"error": "Authentication required", "code": 401}), 401
        return redirect(url_for("auth.login"))

    # ── Configure JWT callbacks ─────────────────────────────────
    from flask import jsonify
    from app.extensions import redis_store

    @jwt.token_in_blocklist_loader
    def check_blocklist(jwt_header, jwt_payload) -> bool:
        """
        Check JWT blocklist — Redis first, DB fallback when Redis is unavailable.

        Redis path  : fast O(1) key lookup, auto-expires with the token.
        DB fallback : queries revoked_tokens table; used when Redis is down.
                      Keeps the blocklist functional even without Redis.
        """
        jti = jwt_payload["jti"]

        # ── Primary: Redis ──────────────────────────────────────
        if redis_store:
            try:
                return redis_store.exists(f"jwt:blocklist:{jti}") > 0
            except Exception:
                pass  # fall through to DB check

        # ── Fallback: database ──────────────────────────────────
        try:
            from app.models.revoked_token import RevokedToken
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            row = RevokedToken.query.filter(
                RevokedToken.jti == jti,
                RevokedToken.expires_at > now,
            ).first()
            return row is not None
        except Exception:
            # If DB check also fails, allow the token — availability over
            # security for the edge case where both Redis and DB are down.
            return False

    @jwt.additional_claims_loader
    def add_claims(identity: str) -> dict:
        """Embed role + email into the token so API routes skip a DB lookup."""
        from app.models.user import User
        user = db.session.get(User, identity)
        if user:
            return {"role": user.role.value, "email": user.email}
        return {}

    @jwt.expired_token_loader
    def expired_token(jwt_header, jwt_payload):
        return jsonify({
            "error": "Token has expired",
            "code":  401,
            "hint":  "POST /api/auth/refresh to get a new access token",
        }), 401

    @jwt.revoked_token_loader
    def revoked_token(jwt_header, jwt_payload):
        return jsonify({"error": "Token has been revoked", "code": 401}), 401

    @jwt.invalid_token_loader
    def invalid_token(reason):
        return jsonify({"error": f"Invalid token: {reason}", "code": 422}), 422

    @jwt.unauthorized_loader
    def missing_token(reason):
        return jsonify({"error": "Authorization header required", "code": 401}), 401

    # ── Import Models (registers tables with SQLAlchemy) ────────
    with app.app_context():
        from app import models  # noqa: F401
        from app.listeners import register_listeners
        register_listeners()

        if app.config.get("DEBUG"):
            db.create_all()

        if not app.config.get("TESTING"):
            _seed_admin(app)

    # ── Register Blueprints ─────────────────────────────────────
    from app.auth import bp as auth_bp
    from app.admin import bp as admin_bp
    from app.student import student_bp
    from app.recruiter import recruiter_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(student_bp, url_prefix="/api/student")
    app.register_blueprint(recruiter_bp, url_prefix="/api/recruiter")

    # ── Health-Check Root ───────────────────────────────────────
    @app.route("/api/health")
    def health():
        return {"status": "ok", "app": "Vantage Recruitment OS"}

    # ── Page Routes ─────────────────────────────────────────────
    from flask import render_template

    # Public
    @app.route("/")
    def landing():
        return render_template("landing_page.html")

    @app.route("/login")
    def login_page():
        return render_template("login_portal.html")

    @app.route("/signup")
    def signup_page():
        return render_template("signup.html")

    @app.route("/change-password")
    def change_password_page():
        return render_template("change_password.html")

    # Student
    @app.route("/student")
    def student_dash():
        return render_template("student_dashboard.html")

    @app.route("/student/profile")
    def student_profile():
        return render_template("student_profile.html")

    @app.route("/student/drives")
    def student_drives():
        return render_template("student_drive_browser.html")

    @app.route("/student/drives/<drive_id>")
    def student_drive_detail(drive_id):
        return render_template("student_drive_detail.html")

    @app.route("/student/applications")
    def student_apps():
        return render_template("student_applications_tracker.html")

    # Admin
    @app.route("/admin")
    def admin_dash():
        return render_template("admin_dashboard.html")

    @app.route("/admin/students")
    def admin_students():
        return render_template("admin_students.html")

    @app.route("/admin/drives")
    def admin_drives():
        return render_template("admin_drives.html")

    @app.route("/admin/drives/<drive_id>")
    def admin_drive_detail(drive_id):
        return render_template("admin_drive_detail.html")

    @app.route("/admin/drives/<drive_id>/applicants")
    def admin_applicants(drive_id):
        return render_template("applicants_manager.html")

    @app.route("/admin/candidates")
    def admin_candidates():
        return render_template("candidate_browser_admin.html")

    @app.route("/admin/candidates/compare")
    def admin_compare():
        return render_template("candidate_comparison.html")

    @app.route("/admin/tsheets")
    def admin_tsheets():
        return render_template("t_sheet_portal.html")

    @app.route("/admin/tsheets/conflicts/<batch_id>")
    def tsheet_conflicts(batch_id):
        return render_template("tsheet_conflicts.html")

    @app.route("/admin/analytics")
    def admin_analytics():
        return render_template("placement_analytics.html")

    @app.route("/admin/recruiters")
    def admin_recruiters():
        return render_template("admin_recruiters.html")

    # Recruiter
    @app.route("/recruiter")
    def recruiter_dash():
        return render_template("recruiter_dashboard.html")

    @app.route("/recruiter/drives")
    def recruiter_drives():
        return render_template("recruiter_my_drives.html")

    @app.route("/recruiter/drives/<drive_id>")
    def recruiter_drive_detail(drive_id):
        return render_template("recruiter_drive_detail.html")

    @app.route("/recruiter/candidates")
    def recruiter_candidates():
        return render_template("recruiter_talent_pool.html")

    @app.route("/recruiter/analytics")
    def recruiter_analytics():
        return render_template("recruiter_analytics.html")

    return app


def _seed_admin(app) -> None:
    """Create admin user from env vars on first startup if none exists."""
    from app.models.user import User
    from app.models.enums import RoleEnum

    admin_email    = os.environ.get("ADMIN_EMAIL", "admin@vantage.os")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    # Enforce a minimum password length — a weak or missing admin password
    # is a critical security misconfiguration that must be caught at boot.
    if not admin_password or len(admin_password) < 16:
        sys.exit(
            "FATAL: ADMIN_PASSWORD must be set and at least 16 characters long.\n"
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(24))\"\n"
            "Then set it in your .env file."
        )

    existing = User.query.filter(
        User.role.in_([RoleEnum.ADMIN, RoleEnum.OWNER])
    ).first()
    if existing:
        return

    admin = User(
        email=admin_email,
        username="admin",
        role=RoleEnum.ADMIN,
        is_active=True,
        is_verified=True,
    )
    admin.set_password(admin_password)
    db.session.add(admin)
    db.session.commit()
    app.logger.info("[Vantage] Admin seeded → %s", admin_email)
