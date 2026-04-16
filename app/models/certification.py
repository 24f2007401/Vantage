"""
Vantage — Certification Models
Master certification list + student←→certification junction table.
"""
from datetime import datetime, timezone
from app.extensions import db


class Certification(db.Model):
    """Pre-approved master list of globally recognised certifications."""
    __tablename__ = "certification"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    name = db.Column(db.String(200), nullable=False, unique=True)
    issuing_body = db.Column(db.String(100), nullable=False)
    short_name = db.Column(db.String(50), nullable=True)
    category = db.Column(db.String(50), nullable=True)  # "Cloud", "Security", etc.
    official_verify_url = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)

    # ── Relationships ───────────────────────────────────────────
    student_certifications = db.relationship(
        "StudentCertification", backref="certification", lazy="dynamic"
    )

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_cert_name", "name"),
        db.Index("ix_cert_issuer", "issuing_body"),
        db.Index(
            "ix_cert_active", "is_active",
            postgresql_where=db.text("is_active = TRUE"),
        ),
    )

    def __repr__(self):
        return f"<Certification {self.short_name or self.name}>"


class StudentCertification(db.Model):
    """Links a student to a certification from the master list."""
    __tablename__ = "student_certification"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    student_id = db.Column(db.String(36), db.ForeignKey("student.id"), nullable=False)
    certification_id = db.Column(db.String(36), db.ForeignKey("certification.id"), nullable=False)
    issued_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    certificate_id_no = db.Column(db.String(100), nullable=True)
    proof_url = db.Column(db.String(500), nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    verified_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    added_by_role = db.Column(db.String(20), default="STUDENT")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Constraints ─────────────────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint("student_id", "certification_id", name="uq_student_cert"),
        db.Index("ix_student_cert_student", "student_id"),
        db.Index("ix_student_cert_cert", "certification_id"),
    )

    def __repr__(self):
        return f"<StudentCertification student={self.student_id} cert={self.certification_id}>"


class StudentCertEntry(db.Model):
    """Freeform certification entry submitted directly by a student.
    Uses a provider whitelist but allows any cert name within that provider.
    """
    __tablename__ = "student_cert_entry"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    student_id = db.Column(db.String(36), db.ForeignKey("student.id"), nullable=False)
    provider = db.Column(db.String(100), nullable=False)       # from ALLOWED_PROVIDERS whitelist
    cert_name = db.Column(db.String(255), nullable=False)
    credential_id = db.Column(db.String(255), nullable=True)
    issue_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    verify_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_cert_entry_student", "student_id"),
    )

    def __repr__(self):
        return f"<StudentCertEntry {self.provider}/{self.cert_name}>"
