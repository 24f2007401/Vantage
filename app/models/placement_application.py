"""
Vantage — PlacementApplication Model
Tracks a student's application through a placement drive pipeline.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import ApplicationStatusEnum


class PlacementApplication(db.Model):
    """Student ↔ PlacementDrive application with status pipeline."""
    __tablename__ = "placement_application"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    student_id = db.Column(db.String(36), db.ForeignKey("student.id"), nullable=False)
    drive_id = db.Column(db.String(36), db.ForeignKey("placement_drive.id"), nullable=False)
    applied_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(
        db.Enum(ApplicationStatusEnum),
        nullable=False,
        default=ApplicationStatusEnum.APPLIED,
    )
    offer_ctc = db.Column(db.Numeric(6, 2), nullable=True)  # CTC in LPA on SELECTED
    stage_notes = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = db.Column(db.DateTime, nullable=True)  # soft delete (withdrawal)

    # ── Constraints & Indexes ───────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint("student_id", "drive_id", name="uq_placement_app_student_drive"),
        db.Index("ix_placement_app_student", "student_id"),
        db.Index("ix_placement_app_drive", "drive_id"),
        db.Index("ix_placement_app_status", "status"),
    )

    def __repr__(self):
        return f"<PlacementApplication student={self.student_id} drive={self.drive_id} [{self.status.value}]>"
