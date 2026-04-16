"""
Vantage — StudentSemesterSummary Model
Pre-computed SGPA per semester, maintained by event listener.
"""
from datetime import datetime, timezone
from app.extensions import db


class StudentSemesterSummary(db.Model):
    """Per-student per-semester academic summary — O(1) read."""
    __tablename__ = "student_semester_summary"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    student_id = db.Column(db.String(36), db.ForeignKey("student.id"), nullable=False)
    semester_number = db.Column(db.SmallInteger, nullable=False)
    exam_batch_id = db.Column(db.String(36), db.ForeignKey("exam_batch.id"), nullable=True)

    sgpa = db.Column(db.Numeric(4, 2), nullable=True)
    total_credits_registered = db.Column(db.Numeric(5, 1), nullable=True)
    total_credits_earned = db.Column(db.Numeric(5, 1), nullable=True)
    backlogs_this_semester = db.Column(db.SmallInteger, default=0)

    computed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_latest = db.Column(db.Boolean, default=True)

    # ── Constraints & Indexes ───────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint(
            "student_id", "semester_number", "exam_batch_id",
            name="uq_sem_summary_student_sem_batch",
        ),
        db.Index("ix_sem_summary_student_sem", "student_id", "semester_number"),
    )

    def __repr__(self):
        return f"<SemSummary student={self.student_id} sem={self.semester_number} SGPA={self.sgpa}>"
