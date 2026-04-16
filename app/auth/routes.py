import time
from datetime import datetime, timezone
from flask import request, jsonify, g
from flask_login import login_user, logout_user
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    jwt_required,
    get_jwt,
    get_jwt_identity,
)
from app.auth import bp
from app.extensions import db, redis_store, limiter
from app.models.user import User, RoleEnum
from app.utils.decorators import role_required

COLLEGE_EMAIL_DOMAIN = "mgit.ac.in"


# ──────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ──────────────────────────────────────────────────────────────────

def _enrich_user_dict(user: User) -> dict:
    """Extend User.to_dict() with role-specific display fields (full_name, company_name)."""
    data = user.to_dict()
    from app.models.enums import RoleEnum as _R
    if user.role == _R.RECRUITER and user.recruiter_id:
        from app.models.recruiter import Recruiter
        rec = db.session.get(Recruiter, user.recruiter_id)
        if rec:
            data["full_name"] = rec.company_name
            data["company_name"] = rec.company_name
    elif user.role == _R.STUDENT and user.student_id:
        from app.models.student import Student as _S
        stu = db.session.get(_S, user.student_id)
        if stu and stu.full_name:
            data["full_name"] = stu.full_name
    if not data.get("full_name"):
        data["full_name"] = user.username
    return data


def _build_token_pair(user: User) -> dict:
    """Create an access + refresh token pair for a given user."""
    access  = create_access_token(identity=user.id)
    refresh = create_refresh_token(identity=user.id)
    return {
        "access_token":  access,
        "refresh_token": refresh,
        "token_type":    "Bearer",
        "expires_in":    900,
    }


def _revoke_jti(jti: str, exp: float) -> None:
    """
    Revoke a single JTI.

    Primary:  Write to Redis with TTL matching token expiry (fast, auto-purging).
    Fallback: Write to the revoked_tokens DB table so logout is durable even
              when Redis is unavailable.
    """
    remaining = max(0, int(exp - time.time()))
    if remaining <= 0:
        return  # Token already expired — nothing to revoke

    # ── Redis path ────────────────────────────────────────────────
    if redis_store:
        try:
            redis_store.set(f"jwt:blocklist:{jti}", "revoked", ex=remaining)
            return  # Redis succeeded — no need for DB fallback
        except Exception:
            pass  # Fall through to DB

    # ── DB fallback ───────────────────────────────────────────────
    try:
        from app.models.revoked_token import RevokedToken
        RevokedToken.revoke(jti, exp)
        db.session.commit()
    except Exception:
        pass  # Best-effort; token will expire naturally if both paths fail


def _revoke_all_for_user(user_id: str) -> None:
    """Invalidate every token issued before NOW for this user (logout everywhere)."""
    if redis_store:
        redis_store.set(f"jwt:user_revoked_at:{user_id}", str(time.time()))


# ──────────────────────────────────────────────────────────────────
#  POST /api/auth/signup
# ──────────────────────────────────────────────────────────────────

@bp.route("/signup", methods=["POST"])
@limiter.limit("5 per minute")
def signup():
    """
    Student signup: { "email": college_email, "password": str, "roll_number": str, "role": "student" }
    Recruiter signup: { "email": str, "password": str, "company_name": str, "role": "recruiter" }

    Returns:
        201  { access_token, refresh_token, token_type, expires_in, user }
        400  missing/invalid fields
        409  email or roll_number already registered
        422  roll_number not found in database (student only)
    """
    from app.models.student import Student
    from app.models.recruiter import Recruiter

    data = request.get_json(silent=True) or {}

    role_str  = (data.get("role") or "").strip().lower()
    email     = (data.get("email") or "").strip().lower()
    password  = data.get("password") or ""

    if role_str not in ("student", "recruiter"):
        return jsonify({"error": "role must be 'student' or 'recruiter'", "code": 400}), 400

    if not email or not password:
        return jsonify({"error": "Email and password are required", "code": 400}), 400

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters", "code": 400}), 400

    # Check email uniqueness
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered", "code": 409}), 409

    if role_str == "student":
        roll_number = (data.get("roll_number") or "").strip().upper()
        if not roll_number:
            return jsonify({"error": "roll_number is required for student signup", "code": 400}), 400

        if not email.endswith(f"@{COLLEGE_EMAIL_DOMAIN}"):
            return jsonify({
                "error": f"Students must use their college email (@{COLLEGE_EMAIL_DOMAIN})",
                "code": 400
            }), 400

        # Roll number must exist — admin uploads T-sheet first
        student = Student.query.filter_by(roll_number=roll_number).first()
        if not student:
            return jsonify({
                "error": "Roll number not found. Contact admin — your T-sheet may not be uploaded yet.",
                "code": 422
            }), 422

        # Roll number must not already have an auth account
        if User.query.filter_by(student_id=student.id).first():
            return jsonify({"error": "Roll number already has an account", "code": 409}), 409

        user = User(
            email=email,
            username=roll_number.lower(),
            role=RoleEnum.STUDENT,
            student_id=student.id,
            is_active=True,
            is_verified=False,
        )
        user.set_password(password)

        # Persist college email on student record if not already set
        if not student.college_email:
            student.college_email = email

        db.session.add(user)
        db.session.commit()

    else:  # recruiter
        company_name = (data.get("company_name") or "").strip()
        if not company_name:
            return jsonify({"error": "company_name is required for recruiter signup", "code": 400}), 400

        recruiter = Recruiter(company_name=company_name, contact_email=email)
        db.session.add(recruiter)
        db.session.flush()  # get recruiter.id before commit

        user = User(
            email=email,
            username=email.split("@")[0],
            role=RoleEnum.RECRUITER,
            recruiter_id=recruiter.id,
            is_active=True,
            is_verified=False,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

    login_user(user)
    tokens = _build_token_pair(user)
    return jsonify({
        "message": "Account created successfully",
        **tokens,
        "user": _enrich_user_dict(user),
    }), 201


# ──────────────────────────────────────────────────────────────────
#  POST /api/auth/login
# ──────────────────────────────────────────────────────────────────

@bp.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    """
    Accepts JSON body: { "email": str, "password": str, "remember_me": bool }

    Returns:
        200  { access_token, refresh_token, token_type, expires_in, user }
        400  missing fields
        401  invalid credentials
        403  account disabled or unverified

    The frontend/ static UIs call this endpoint and store tokens in memory.
    The session cookie is also set so that server-rendered pages work without
    passing a token in the header.
    """
    data = request.get_json(silent=True) or {}

    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required", "code": 400}), 400

    # Always run password check even when user not found — prevents timing-based
    # user enumeration attacks.
    user = User.query.filter_by(email=email).first()
    if not user:
        import bcrypt
        # Hash the password to simulate the time taken for a real check
        bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
        return jsonify({"error": "Invalid credentials", "code": 401}), 401

    if not user.verify_password(password):
        return jsonify({"error": "Invalid credentials", "code": 401}), 401

    if not user.is_active:
        return jsonify({"error": "Account is disabled", "code": 403}), 403

    # Update last_login
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    # Set Flask-Login session cookie (for any server-rendered pages)
    remember = bool(data.get("remember_me", False))
    login_user(user, remember=remember)

    # Build JWT pair (for frontend/ SPA calls)
    tokens = _build_token_pair(user)

    return jsonify({
        "message": "Login successful",
        **tokens,
        "user": _enrich_user_dict(user),
    }), 200


# ──────────────────────────────────────────────────────────────────
#  POST /api/auth/logout
# ──────────────────────────────────────────────────────────────────

@bp.route("/logout", methods=["POST"])
@role_required(RoleEnum.VIEWER)
def logout():
    """
    Revokes the current JWT and clears the session cookie.

    Optional JSON body: { "all_devices": true }
    → If all_devices is true, invalidates ALL tokens for this user (logout everywhere).

    Returns:
        200  { message }
        401  not authenticated
    """
    data          = request.get_json(silent=True) or {}
    all_devices   = bool(data.get("all_devices", False))
    user          = g.current_user

    if all_devices:
        _revoke_all_for_user(user.id)
    elif g.auth_source == "jwt":
        claims = get_jwt()
        _revoke_jti(claims["jti"], claims["exp"])

    logout_user()  # Always clear the session cookie
    return jsonify({"message": "Logged out successfully"}), 200


# ──────────────────────────────────────────────────────────────────
#  POST /api/auth/refresh
# ──────────────────────────────────────────────────────────────────

@bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """
    Exchange a valid refresh token for a new access + refresh pair.

    Client flow:
      1. Access token expires → receive 401 with hint
      2. POST /api/auth/refresh  (Authorization: Bearer <refresh_token>)
      3. Receive new token pair
      4. Retry original request with the new access token

    The old refresh token is revoked on every call (rotation pattern —
    prevents replay attacks if a refresh token is stolen).

    Returns:
        200  { access_token, refresh_token, token_type, expires_in }
        401  invalid, expired, or revoked refresh token
    """
    claims  = get_jwt()
    user_id = get_jwt_identity()

    # Rotate: revoke the used refresh token immediately
    _revoke_jti(claims["jti"], claims["exp"])

    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        return jsonify({"error": "User not found or disabled", "code": 401}), 401

    return jsonify(_build_token_pair(user)), 200


# ──────────────────────────────────────────────────────────────────
#  GET /api/auth/me
# ──────────────────────────────────────────────────────────────────

@bp.route("/me", methods=["GET"])
@role_required(RoleEnum.VIEWER)
def me():
    """
    Returns the authenticated user's profile.
    Works from either a session cookie or a JWT bearer token.

    Used by the frontend/ UIs to bootstrap the logged-in state on page load.

    Returns:
        200  { id, email, username, role, is_active, is_verified, needs_password_change, created_at, last_login }
        401  not authenticated
    """
    return jsonify(_enrich_user_dict(g.current_user)), 200


# ──────────────────────────────────────────────────────────────────
#  POST /api/auth/change-password
# ──────────────────────────────────────────────────────────────────

@bp.route("/change-password", methods=["POST"])
@role_required(RoleEnum.VIEWER)
def change_password():
    """
    Change the authenticated user's password.
    Required for students whose accounts were created by the T-sheet pipeline
    (needs_password_change=True).

    JSON body: { "current_password": str, "new_password": str }

    Returns:
        200  { message }
        400  missing fields or new password too short
        401  current password incorrect
    """
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""

    if not current_password or not new_password:
        return jsonify({"error": "current_password and new_password required", "code": 400}), 400

    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters", "code": 400}), 400

    user = g.current_user
    if not user.verify_password(current_password):
        return jsonify({"error": "Current password is incorrect", "code": 401}), 401

    user.set_password(new_password)
    user.needs_password_change = False
    db.session.commit()

    return jsonify({"message": "Password changed successfully"}), 200
