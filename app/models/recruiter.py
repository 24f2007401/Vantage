"""
Vantage — Recruiter Model
External company HR representative.
"""
from datetime import datetime, timezone
from app.extensions import db


class Recruiter(db.Model):
    """Recruiter / company entity."""
    __tablename__ = "recruiter"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    company_name = db.Column(db.String(200), nullable=False)
    company_website = db.Column(db.String(300), nullable=True)
    sector = db.Column(db.String(100), nullable=True)  # "IT", "Core Engineering", etc.
    contact_name = db.Column(db.String(200), nullable=True)
    contact_email = db.Column(db.String(150), nullable=True)
    contact_phone = db.Column(db.String(15), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    placement_drives = db.relationship("PlacementDrive", backref="recruiter", lazy="dynamic")

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_recruiter_company", "company_name"),
    )

    def __repr__(self):
        return f"<Recruiter {self.company_name}>"
