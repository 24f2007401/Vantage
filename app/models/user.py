"""
Vantage — User Model (Auth Table)
Merged from Module 2 (schema structure, FKs) and Module 3 (auth behavior, bcrypt).
"""
from datetime import datetime, timezone
import bcrypt
from flask_login import UserMixin
from app.extensions import db
from app.models.enums import RoleEnum


class User(UserMixin, db.Model):
    """Central authentication record — links to Student or Recruiter."""
    __tablename__ = "users"  # Changed from 'user' per Module 3 spec

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    _password = db.Column("password_hash", db.String(72), nullable=False)
    role = db.Column(db.Enum(RoleEnum), nullable=False, default=RoleEnum.VIEWER)

    # ── Role-specific links (from Module 2) ─────────────────────
    student_id = db.Column(
        db.String(36), db.ForeignKey("student.id"), unique=True, nullable=True
    )
    recruiter_id = db.Column(
        db.String(36), db.ForeignKey("recruiter.id"), unique=True, nullable=True
    )

    # ── Account lifecycle flags ─────────────────────────────────
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    needs_password_change = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    jwt_issued_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # ── Password management ─────────────────────────────────────
    def set_password(self, raw_password: str) -> None:
        """Hash with bcrypt work factor 12."""
        if len(raw_password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        self._password = bcrypt.hashpw(
            raw_password.encode("utf-8"),
            bcrypt.gensalt(rounds=12)
        ).decode("utf-8")

    def verify_password(self, raw_password: str) -> bool:
        """Constant-time comparison — safe against timing attacks."""
        return bcrypt.checkpw(
            raw_password.encode("utf-8"),
            self._password.encode("utf-8")
        )

    # ── Flask-Login interface ───────────────────────────────────
    def get_id(self) -> str:
        return self.id

    @property
    def is_authenticated(self) -> bool:
        return self.is_active

    # ── Serialisation ───────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "role": self.role.value,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "needs_password_change": self.needs_password_change,
            "created_at": self.created_at.isoformat(),
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    def __repr__(self):
        return f"<User {self.email} [{self.role.value}]>"
