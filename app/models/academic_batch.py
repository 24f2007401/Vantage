"""
Vantage — AcademicBatch Model
A cohort of students — e.g., "2021–2025 Regular Batch, CSE".
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import BatchTypeEnum


class AcademicBatch(db.Model):
    """Student cohort identified by branch, admission year, and type."""
    __tablename__ = "academic_batch"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    branch_id = db.Column(db.String(36), db.ForeignKey("branch.id"), nullable=False)
    admission_year = db.Column(db.SmallInteger, nullable=False)  # 2021
    graduation_year = db.Column(db.SmallInteger, nullable=False)  # 2025
    admission_type = db.Column(db.Enum(BatchTypeEnum), nullable=False)
    jntuh_batch_prefix = db.Column(
        db.String(10), nullable=True
    )  # "21261A" for Regular CSE 2021, "21265A" for Lateral
    total_students = db.Column(db.SmallInteger, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    students = db.relationship("Student", backref="academic_batch", lazy="dynamic")

    # ── Constraints ─────────────────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint(
            "branch_id", "admission_year", "admission_type",
            name="uq_batch_branch_year_type"
        ),
    )

    def __repr__(self):
        return f"<AcademicBatch {self.admission_year}-{self.graduation_year} {self.admission_type.value}>"
