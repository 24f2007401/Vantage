"""
Vantage — PlacementDrive Model
A recruitment drive (job posting) created by a recruiter.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import DriveTypeEnum, DriveStatusEnum


class PlacementDrive(db.Model):
    """Recruitment drive with attached eligibility rules."""
    __tablename__ = "placement_drive"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    recruiter_id = db.Column(db.String(36), db.ForeignKey("recruiter.id"), nullable=False)
    drive_title = db.Column(db.String(200), nullable=False)
    role_title = db.Column(db.String(200), nullable=False)
    ctc_lpa = db.Column(db.Numeric(6, 2), nullable=True)
    job_location = db.Column(db.String(200), nullable=True)
    job_type = db.Column(db.Enum(DriveTypeEnum), nullable=False)
    application_open_at = db.Column(db.DateTime, nullable=True)
    application_close_at = db.Column(db.DateTime, nullable=True)
    exam_date = db.Column(db.Date, nullable=True)
    status = db.Column(
        db.Enum(DriveStatusEnum), nullable=False, default=DriveStatusEnum.DRAFT
    )
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationships ───────────────────────────────────────────
    eligibility_rules = db.relationship(
        "EligibilityRule", backref="placement_drive", lazy="dynamic"
    )
    applications = db.relationship(
        "PlacementApplication", backref="placement_drive", lazy="dynamic"
    )
    messages = db.relationship("DriveMessage", backref="placement_drive", lazy="dynamic")

    def __repr__(self):
        return f"<PlacementDrive {self.drive_title} [{self.status.value}]>"
