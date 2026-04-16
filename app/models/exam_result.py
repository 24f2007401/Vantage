"""
Vantage — ExamResult Model ⭐ (Highest-Volume Table)
One row per student × subject × examination event.
Supports attempt tracking and superseding for supplementary results.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import GradeLetterEnum


class ExamResult(db.Model):
    """Atomic unit of academic data — one grade per student per subject per exam."""
    __tablename__ = "exam_result"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    student_id = db.Column(db.String(36), db.ForeignKey("student.id"), nullable=False)
    exam_batch_id = db.Column(db.String(36), db.ForeignKey("exam_batch.id"), nullable=False)
    course_id = db.Column(db.String(36), db.ForeignKey("course.id"), nullable=False)

    # ── RAW MARKS (as in T-Sheet) ───────────────────────────────
    cie_marks = db.Column(db.SmallInteger, nullable=True)  # 0–30
    see_marks = db.Column(db.SmallInteger, nullable=True)  # 0–70; NULL if absent or MC
    total_marks = db.Column(db.SmallInteger, nullable=True)  # 0–100

    # ── GRADE DATA ──────────────────────────────────────────────
    grade_letter = db.Column(db.Enum(GradeLetterEnum), nullable=False)
    grade_points = db.Column(db.SmallInteger, nullable=False)  # 0–10
    credits_awarded = db.Column(
        db.Numeric(3, 1), nullable=False
    )  # 0.0 if F/Ab, else course.credits

    # ── SPECIAL FLAGS ───────────────────────────────────────────
    is_absent = db.Column(db.Boolean, default=False)  # TRUE when SEE = "Ab"
    is_mandatory_nc = db.Column(db.Boolean, default=False)  # TRUE for MC results
    is_withheld = db.Column(db.Boolean, default=False)

    # ── ATTEMPT TRACKING ────────────────────────────────────────
    attempt_number = db.Column(db.SmallInteger, default=1)
    superseded_by = db.Column(
        db.String(36),
        db.ForeignKey("exam_result.id"),
        nullable=True,
    )  # Self-ref: original F row points to passing supplementary result

    # ── SOURCE TRACING ──────────────────────────────────────────
    tsheet_record_id = db.Column(
        db.String(36), db.ForeignKey("tsheet_record.id"), nullable=True
    )

    # ── AUDIT ───────────────────────────────────────────────────
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Constraints & Indexes ───────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint(
            "student_id", "exam_batch_id", "course_id", "attempt_number",
            name="uq_result_student_exam_course_attempt",
        ),
        db.Index("ix_result_student", "student_id"),
        db.Index("ix_result_student_exam", "student_id", "exam_batch_id"),
        db.Index("ix_result_course_grade", "course_id", "grade_letter"),
        db.Index("ix_result_batch_grade", "exam_batch_id", "grade_letter"),
        db.Index(
            "ix_result_absent",
            "is_absent",
            postgresql_where=db.text("is_absent = TRUE"),
        ),
        db.Index(
            "ix_result_active",
            "superseded_by",
            postgresql_where=db.text("superseded_by IS NULL"),
        ),
    )

    def __repr__(self):
        return f"<ExamResult {self.student_id} {self.course_id} [{self.grade_letter.value}]>"
