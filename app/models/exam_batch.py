"""
Vantage — ExamBatch Model
One row per examination event. Each uploaded T-Sheet corresponds to
exactly one ExamBatch.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import ExamTypeEnum


class ExamBatch(db.Model):
    """Examination event — Regular, Supplementary, or Improvement."""
    __tablename__ = "exam_batch"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    branch_id = db.Column(db.String(36), db.ForeignKey("branch.id"), nullable=False)
    semester_number = db.Column(db.SmallInteger, nullable=False)  # 1–8
    exam_type = db.Column(db.Enum(ExamTypeEnum), nullable=False)
    exam_month = db.Column(db.SmallInteger, nullable=False)  # 1–12
    exam_year = db.Column(db.SmallInteger, nullable=False)  # 2025
    exam_date = db.Column(db.Date, nullable=True)
    recount_deadline = db.Column(db.Date, nullable=True)  # from Row 3
    academic_year = db.Column(db.String(20), nullable=True)  # "2024-25"

    tsheet_upload_id = db.Column(
        db.String(36), db.ForeignKey("tsheet_upload.id"), nullable=True
    )
    raw_header_text = db.Column(db.Text, nullable=True)  # Row 4 verbatim

    total_students = db.Column(db.SmallInteger, nullable=True)
    ingested_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    exam_results = db.relationship("ExamResult", backref="exam_batch", lazy="dynamic")
    semester_summaries = db.relationship(
        "StudentSemesterSummary", backref="exam_batch", lazy="dynamic"
    )

    # ── Constraints & Indexes ───────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint(
            "branch_id", "semester_number", "exam_type", "exam_year", "exam_month",
            name="uq_exam_batch_identity",
        ),
        db.Index("ix_exam_batch_branch_sem", "branch_id", "semester_number"),
        db.Index("ix_exam_batch_year_month", "exam_year", "exam_month"),
    )

    def __repr__(self):
        return f"<ExamBatch Sem{self.semester_number} {self.exam_type.value} {self.exam_month}/{self.exam_year}>"
