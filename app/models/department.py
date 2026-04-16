"""
Vantage — Department Model
A department within an institution (e.g., CSE, MME).
"""
from datetime import datetime, timezone
from app.extensions import db


class Department(db.Model):
    """Academic department — mapped from T-Sheet Row 4 exam descriptor."""
    __tablename__ = "department"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    institution_id = db.Column(db.String(36), db.ForeignKey("institution.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)  # "Computer Science and Engineering"
    short_name = db.Column(db.String(20), nullable=False)  # "CSE"
    tsheet_name = db.Column(
        db.String(200), nullable=True
    )  # Exact uppercase string from Row 4, used for parser case-insensitive match
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    branches = db.relationship("Branch", backref="department", lazy="dynamic")
    courses = db.relationship("Course", backref="department", lazy="dynamic")

    # ── Constraints ─────────────────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint("institution_id", "short_name", name="uq_dept_inst_short"),
        db.Index("ix_department_short_name", "short_name"),
        db.Index("ix_department_tsheet_name", "tsheet_name"),
    )

    def __repr__(self):
        return f"<Department {self.short_name}>"
