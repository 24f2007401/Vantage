"""
Vantage — Course Model
A subject identified by its JNTUH course code.
MC courses carry is_mandatory_non_credit = TRUE.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import CourseTypeEnum


class Course(db.Model):
    """Academic course / subject."""
    __tablename__ = "course"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    course_code = db.Column(db.String(10), nullable=False, unique=True)  # "CS801PC"
    course_name = db.Column(db.String(200), nullable=False)
    department_id = db.Column(
        db.String(36), db.ForeignKey("department.id"), nullable=True
    )  # NULL for cross-department courses
    course_type = db.Column(db.Enum(CourseTypeEnum), nullable=False)
    semester_number = db.Column(db.SmallInteger, nullable=False)
    credits = db.Column(db.Numeric(3, 1), nullable=False)
    max_cie_marks = db.Column(db.SmallInteger, default=30)
    max_see_marks = db.Column(db.SmallInteger, nullable=True)  # NULL for MC (CIE-only)
    max_total_marks = db.Column(db.SmallInteger, default=100)
    is_mandatory_non_credit = db.Column(db.Boolean, default=False)  # TRUE for MC
    is_lab = db.Column(db.Boolean, default=False)
    is_project = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    exam_results = db.relationship("ExamResult", backref="course", lazy="dynamic")

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_course_code", "course_code"),
        db.Index("ix_course_sem_dept", "semester_number", "department_id"),
    )

    def __repr__(self):
        return f"<Course {self.course_code} — {self.course_name}>"
