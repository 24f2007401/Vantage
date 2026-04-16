"""
Vantage — Institution Model
Stores MGIT as a record. Designed for multi-institution extensibility.
"""
from datetime import datetime, timezone
from app.extensions import db


class Institution(db.Model):
    """MGIT institutional record."""
    __tablename__ = "institution"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    name = db.Column(db.String(200), nullable=False)
    short_name = db.Column(db.String(20), nullable=False)  # "MGIT"
    address = db.Column(db.Text, nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    jntuh_code = db.Column(db.String(10), unique=True, nullable=True)  # "26" for MGIT
    is_autonomous = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    departments = db.relationship("Department", backref="institution", lazy="dynamic")

    def __repr__(self):
        return f"<Institution {self.short_name}>"
