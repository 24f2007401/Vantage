"""
Vantage — TSheetUpload Model
Tracks every file upload by an admin. Duplicate detection via SHA-256.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import UploadStatusEnum, ExamTypeEnum


class TSheetUpload(db.Model):
    """Raw ingestion event — one row per uploaded T-Sheet file."""
    __tablename__ = "tsheet_upload"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    uploaded_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    original_filename = db.Column(db.String(300), nullable=False)
    stored_path = db.Column(db.String(500), nullable=True)
    file_size_bytes = db.Column(db.Integer, nullable=True)
    file_hash_sha256 = db.Column(db.String(64), unique=True, nullable=True)

    upload_status = db.Column(
        db.Enum(UploadStatusEnum), nullable=False, default=UploadStatusEnum.PENDING
    )

    # ── Detected metadata from Row 4 parse ──────────────────────
    branch_detected = db.Column(db.String(20), nullable=True)
    semester_detected = db.Column(db.SmallInteger, nullable=True)
    exam_type_detected = db.Column(db.Enum(ExamTypeEnum), nullable=True)
    exam_month_detected = db.Column(db.SmallInteger, nullable=True)
    exam_year_detected = db.Column(db.SmallInteger, nullable=True)

    # ── Pipeline stage tracker ──────────────────────────────────
    # Values: uploading | parsing | normalizing | ingesting | completed | failed
    current_stage = db.Column(db.String(20), nullable=True)

    # ── Processing stats ────────────────────────────────────────
    total_rows_raw = db.Column(db.Integer, nullable=True)
    total_records_parsed = db.Column(db.Integer, nullable=True)
    total_conflicts = db.Column(db.Integer, default=0)
    total_duplicates = db.Column(db.Integer, default=0)
    new_students_created = db.Column(db.Integer, default=0)
    new_branches_flagged = db.Column(db.Integer, default=0)
    processing_log = db.Column(db.Text, nullable=True)  # JSON event log
    error_message = db.Column(db.Text, nullable=True)

    # ── Timestamps ──────────────────────────────────────────────
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    processing_started_at = db.Column(db.DateTime, nullable=True)
    processing_completed_at = db.Column(db.DateTime, nullable=True)

    # ── Relationships ───────────────────────────────────────────
    records = db.relationship("TSheetRecord", backref="tsheet_upload", lazy="dynamic")
    exam_batches = db.relationship("ExamBatch", backref="tsheet_upload", lazy="dynamic")

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_tsheet_upload_by", "uploaded_by"),
        db.Index("ix_tsheet_upload_status", "upload_status"),
        db.Index("ix_tsheet_upload_hash", "file_hash_sha256"),
    )

    def __repr__(self):
        return f"<TSheetUpload {self.original_filename} [{self.upload_status.value}]>"
