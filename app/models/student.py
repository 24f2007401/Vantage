"""
Vantage — Student Model ⭐ (Core Table)
The central entity with ~40 columns covering identity, biodata, academic
summary, placement metadata, and soft delete.
All biodata is admin-entered; academic summary is maintained by event listeners.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import (
    GenderEnum, CategoryEnum, ProfileStatusEnum,
)


class Student(db.Model):
    """Core student record — created on first T-Sheet encounter."""
    __tablename__ = "student"

    # ── PRIMARY IDENTITY ────────────────────────────────────────
    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    roll_number = db.Column(db.String(12), nullable=False, unique=True)
    # "21261A0501", max 12 chars for hex extended serials

    # ── BATCH & BRANCH LINKAGE ──────────────────────────────────
    academic_batch_id = db.Column(
        db.String(36), db.ForeignKey("academic_batch.id"), nullable=False
    )

    # ── PERSONAL BIODATA (Admin-entered, all nullable) ──────────
    full_name = db.Column(db.String(200), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.Enum(GenderEnum), nullable=True)
    category = db.Column(db.Enum(CategoryEnum), nullable=True)
    nationality = db.Column(db.String(50), default="Indian")
    aadhar_last4 = db.Column(db.String(4), nullable=True)

    # ── CONTACT INFORMATION (Admin-entered) ─────────────────────
    college_email = db.Column(db.String(150), nullable=True, unique=True)
    personal_email = db.Column(db.String(150), nullable=True)
    phone_primary = db.Column(db.String(15), nullable=True)
    phone_parent = db.Column(db.String(15), nullable=True)
    address_local = db.Column(db.Text, nullable=True)
    address_permanent = db.Column(db.Text, nullable=True)

    # ── STUDENT-EDITABLE PROFILE FIELDS ─────────────────────────
    github_url = db.Column(db.String(500), nullable=True)
    linkedin_url = db.Column(db.String(500), nullable=True)
    portfolio_url = db.Column(db.String(500), nullable=True)
    resume_url = db.Column(db.String(500), nullable=True)
    skills_json = db.Column(db.Text, nullable=True)  # JSON array of skill strings

    # ── ACADEMIC ENTRY DETAILS ──────────────────────────────────
    admission_year = db.Column(db.SmallInteger, nullable=False)
    lateral_entry = db.Column(db.Boolean, default=False)
    admission_quota = db.Column(db.String(50), nullable=True)

    # ── COMPUTED ACADEMIC SUMMARY (Maintained by listeners) ─────
    current_cgpa = db.Column(db.Numeric(4, 2), default=0.00)
    active_backlogs = db.Column(db.SmallInteger, default=0)
    total_backlogs_ever = db.Column(db.SmallInteger, default=0)
    semesters_completed = db.Column(db.SmallInteger, default=0)
    last_tsheet_synced = db.Column(db.DateTime, nullable=True)

    # ── PROFILE STATUS ──────────────────────────────────────────
    profile_status = db.Column(
        db.Enum(ProfileStatusEnum),
        nullable=False,
        default=ProfileStatusEnum.PENDING,
    )
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    verification_source = db.Column(db.String(20), nullable=True)  # "tsheet" or "admin"
    verification_note = db.Column(db.Text, nullable=True)
    verified_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)

    # ── PLACEMENT METADATA ──────────────────────────────────────
    is_placement_eligible = db.Column(db.Boolean, default=False)
    placement_opted_in = db.Column(db.Boolean, default=True)
    placement_opted_in_changed_at = db.Column(db.DateTime, nullable=True)
    final_placed_company = db.Column(db.String(200), nullable=True)
    final_ctc_lpa = db.Column(db.Numeric(6, 2), nullable=True)

    # ── SOFT DELETE ─────────────────────────────────────────────
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)

    # ── AUDIT TIMESTAMPS ────────────────────────────────────────
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ───────────────────────────────────────────
    exam_results = db.relationship("ExamResult", backref="student", lazy="dynamic")
    semester_summaries = db.relationship(
        "StudentSemesterSummary", backref="student", lazy="dynamic"
    )
    placement_applications = db.relationship(
        "PlacementApplication", backref="student", lazy="dynamic"
    )
    certifications = db.relationship(
        "StudentCertification", backref="student", lazy="dynamic"
    )
    message_deliveries = db.relationship(
        "StudentMessageDelivery", backref="student", lazy="dynamic"
    )

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_student_roll_number", "roll_number", unique=True),
        db.Index("ix_student_batch", "academic_batch_id"),
        db.Index("ix_student_cgpa_backlogs", "current_cgpa", "active_backlogs"),
        db.Index("ix_student_backlogs_ever", "total_backlogs_ever"),
        db.Index("ix_student_profile_status", "profile_status"),
        db.Index(
            "ix_student_active",
            "deleted_at",
            postgresql_where=db.text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self):
        return f"<Student {self.roll_number} CGPA={self.current_cgpa}>"
