"""
Vantage — Branch Model
A specific program under a department (e.g., CSE branch code "05").
Parser auto-creates rows for unknown branch codes with needs_review=TRUE.
"""
from datetime import datetime, timezone
from app.extensions import db


class Branch(db.Model):
    """Branch / program — auto-created by parser for unknown codes."""
    __tablename__ = "branch"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    department_id = db.Column(
        db.String(36), db.ForeignKey("department.id"), nullable=True
    )  # NULL until admin resolves needs_review rows
    name = db.Column(db.String(200), nullable=True)  # "Computer Science and Engineering"
    short_name = db.Column(db.String(20), nullable=True)  # "CSE"
    branch_code = db.Column(
        db.String(4), nullable=False, unique=True
    )  # "05" — extracted from roll number position 6-7
    duration_years = db.Column(db.SmallInteger, default=4)
    degree = db.Column(db.String(50), default="B.Tech.")
    is_active = db.Column(db.Boolean, default=True)
    needs_review = db.Column(
        db.Boolean, default=False
    )  # TRUE when parser auto-created from unknown code
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    academic_batches = db.relationship("AcademicBatch", backref="branch", lazy="dynamic")
    exam_batches = db.relationship("ExamBatch", backref="branch", lazy="dynamic")

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_branch_code", "branch_code"),
        db.Index("ix_branch_needs_review", "needs_review", postgresql_where=db.text("needs_review = TRUE")),
    )

    def __repr__(self):
        return f"<Branch {self.short_name or self.branch_code}>"
