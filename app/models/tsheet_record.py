"""
Vantage — TSheetRecord Model
Staging table: every raw row from the Excel file lives here before
being committed to exam_result. Preserves exact source data for audit.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import RecordStatusEnum


class TSheetRecord(db.Model):
    """Raw T-Sheet row — staging before commit to ExamResult."""
    __tablename__ = "tsheet_record"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    tsheet_upload_id = db.Column(
        db.String(36), db.ForeignKey("tsheet_upload.id"), nullable=False
    )

    # ── RAW VALUES (exact strings from Excel) ───────────────────
    raw_ht_no = db.Column(db.String(20), nullable=True)
    raw_course_code = db.Column(db.String(20), nullable=True)
    raw_course_name = db.Column(db.String(200), nullable=True)
    raw_cie_marks = db.Column(db.String(10), nullable=True)  # "22", "Ab", ""
    raw_see_marks = db.Column(db.String(10), nullable=True)
    raw_total_marks = db.Column(db.String(10), nullable=True)
    raw_grade_letter = db.Column(db.String(5), nullable=True)
    raw_grade_points = db.Column(db.String(5), nullable=True)
    raw_credits = db.Column(db.String(10), nullable=True)
    source_sheet_name = db.Column(db.String(100), nullable=True)
    source_row_number = db.Column(db.Integer, nullable=True)

    # ── RESOLUTION STATUS ───────────────────────────────────────
    resolution_status = db.Column(
        db.Enum(RecordStatusEnum), nullable=False, default=RecordStatusEnum.PENDING
    )
    resolved_student_id = db.Column(db.String(36), db.ForeignKey("student.id"), nullable=True)
    resolved_course_id = db.Column(db.String(36), db.ForeignKey("course.id"), nullable=True)
    resolved_result_id = db.Column(db.String(36), db.ForeignKey("exam_result.id"), nullable=True)

    # ── CONFLICT HANDLING ───────────────────────────────────────
    conflict_type = db.Column(db.String(50), nullable=True)
    # 'DUPLICATE'|'GRADE_MISMATCH'|'NEW_STUDENT'|'NEW_BRANCH'|'UNKNOWN_COURSE'
    conflict_detail = db.Column(db.Text, nullable=True)  # JSON
    admin_resolution = db.Column(db.String(20), nullable=True)
    # 'ACCEPT_NEW'|'KEEP_EXISTING'|'SKIP'
    resolved_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    exam_results = db.relationship(
        "ExamResult", 
        backref="tsheet_record", 
        lazy="dynamic",
        foreign_keys="[ExamResult.tsheet_record_id]"
    )

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_tsheet_record_upload_status", "tsheet_upload_id", "resolution_status"),
        db.Index("ix_tsheet_record_ht_no", "raw_ht_no"),
        db.Index("ix_tsheet_record_student", "resolved_student_id"),
    )

    def __repr__(self):
        return f"<TSheetRecord {self.raw_ht_no} [{self.resolution_status.value}]>"
