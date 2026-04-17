from functools import wraps
from flask import jsonify, request, g
from flask_login import current_user as session_user
from flask_jwt_extended import get_jwt_identity, get_jwt
from app.extensions import db, redis_store
from app.models.user import User, RoleEnum


# ──────────────────────────────────────────────────────────────────
#  INTERNAL — resolve the current user from either auth transport
# ──────────────────────────────────────────────────────────────────

def _resolve_user():
    """
    Returns (user, source) where source is "jwt" | "session" | "none".

    Precedence:
      1. JWT bearer token in Authorization header  →  for frontend/ API calls
      2. Flask-Login session cookie                →  for Jinja2 server-rendered pages

    This ensures API clients using Bearer tokens are not accidentally
    authenticated via a stale browser session.
    """
    # ── JWT path ──────────────────────────────────────────────────
    # before_request already called verify_jwt_in_request(optional=True) and
    # stored the decoded payload in g — call get_jwt_identity() directly to
    # avoid a second verification that can conflict with the first.
    if request.headers.get("Authorization", "").startswith("Bearer "):
        try:
            user_id = get_jwt_identity()
            if not user_id:
                return None, "jwt"
            claims = get_jwt()
            user = db.session.get(User, user_id)

            try:
                revoked_at = redis_store.get(f"jwt:user_revoked_at:{user_id}") if redis_store else None
                if revoked_at and claims.get("iat", 0) < float(revoked_at):
                    return None, "jwt"
            except Exception:
                # Redis inactive, skip global revocation check
                pass

            return user, "jwt"
        except Exception:
            return None, "jwt"

    # ── Session path ──────────────────────────────────────────────
    if session_user.is_authenticated:
        return session_user._get_current_object(), "session"

    return None, "none"


# ──────────────────────────────────────────────────────────────────
#  @role_required
# ──────────────────────────────────────────────────────────────────

def role_required(*required_roles: RoleEnum):
    """
    Enforces a minimum role level. Supports both hierarchical and exact-match modes.

    HIERARCHICAL (single role argument) — recommended for most routes:
        @role_required(RoleEnum.ADMIN)
        → passes ADMIN and OWNER; blocks STUDENT, RECRUITER, VIEWER

        @role_required(RoleEnum.STUDENT)
        → passes STUDENT, RECRUITER, ADMIN, OWNER; blocks VIEWER

    EXACT MATCH (multiple role arguments):
        @role_required(RoleEnum.STUDENT, RoleEnum.RECRUITER)
        → passes only STUDENT or RECRUITER; blocks ADMIN, OWNER, VIEWER

    After the decorator succeeds:
        g.current_user  →  the resolved User ORM object
        g.auth_source   →  "jwt" | "session"

    Returns 401 if not authenticated, 403 if role is insufficient.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user, source = _resolve_user()

            if user is None or not user.is_active:
                return jsonify({"error": "Authentication required", "code": 401}), 401

            if len(required_roles) == 1:
                # Hierarchical check
                if not (user.role >= required_roles[0]):
                    return jsonify({
                        "error":    "Insufficient permissions",
                        "code":     403,
                        "required": required_roles[0].value,
                        "current":  user.role.value,
                    }), 403
            else:
                # Exact-match check — user must be one of the listed roles
                if user.role not in required_roles:
                    return jsonify({
                        "error":    "Insufficient permissions",
                        "code":     403,
                        "required": [r.value for r in required_roles],
                        "current":  user.role.value,
                    }), 403

            g.current_user = user
            g.auth_source  = source
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ──────────────────────────────────────────────────────────────────
#  @verified_required
# ──────────────────────────────────────────────────────────────────

def verified_required(fn):
    """
    Requires the user to have is_verified=True.
    Use this on routes that need a complete, verified account —
    e.g., a student applying to a drive, or a recruiter posting a job.

    Stack it AFTER @role_required:
        @role_required(RoleEnum.STUDENT)
        @verified_required
        def apply_to_drive():
            ...
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user, source = _resolve_user()

        if user is None or not user.is_active:
            return jsonify({"error": "Authentication required", "code": 401}), 401

        if not user.is_verified:
            return jsonify({
                "error": "Account not verified. Check your email for the verification link.",
                "code":  403,
            }), 403

        g.current_user = user
        g.auth_source  = source
        return fn(*args, **kwargs)
    return wrapper
