"""
Vantage — EligibilityRule Model
Per-drive eligibility criteria with backlog policy toggle.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import BacklogPolicyEnum


class EligibilityRule(db.Model):
    """Eligibility criteria for a placement drive (or institution default)."""
    __tablename__ = "eligibility_rule"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    drive_id = db.Column(
        db.String(36), db.ForeignKey("placement_drive.id"), nullable=True
    )  # NULL = institution-level default rule
    rule_name = db.Column(db.String(100), nullable=True)
    min_cgpa = db.Column(db.Numeric(4, 2), default=0.00)
    max_active_backlogs = db.Column(db.SmallInteger, default=999)
    backlog_policy = db.Column(
        db.Enum(BacklogPolicyEnum), nullable=False, default=BacklogPolicyEnum.ACTIVE_ONLY
    )
    mc_pass_required = db.Column(db.Boolean, default=True)
    allowed_branches = db.Column(db.Text, nullable=True)  # JSON array
    max_admission_year = db.Column(db.SmallInteger, nullable=True)
    min_semesters_completed = db.Column(db.SmallInteger, default=6)
    additional_criteria = db.Column(db.Text, nullable=True)  # JSON
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_elig_rule_drive", "drive_id"),
        db.Index(
            "ix_elig_rule_active", "is_active",
            postgresql_where=db.text("is_active = TRUE"),
        ),
    )

    def evaluate(self, student) -> tuple:
        """
        Evaluate whether a student meets this rule's criteria.
        Returns (is_eligible: bool, reasons: list[str]).
        """
        import json as _json
        reasons = []

        # CGPA check
        if self.min_cgpa is not None and student.current_cgpa is not None:
            if float(student.current_cgpa) < float(self.min_cgpa):
                reasons.append(f"CGPA below minimum ({self.min_cgpa} required, you have {student.current_cgpa})")

        # Active backlogs check
        if self.max_active_backlogs is not None:
            if (student.active_backlogs or 0) > self.max_active_backlogs:
                reasons.append(f"Active backlogs exceed limit ({self.max_active_backlogs} max, you have {student.active_backlogs})")

        # Backlog policy: NEVER_HAD requires zero historical backlogs
        if self.backlog_policy == BacklogPolicyEnum.NEVER_HAD:
            if (student.total_backlogs_ever or 0) > 0:
                reasons.append("This drive requires no history of backlogs (NEVER_HAD policy)")

        # Branch check
        if self.allowed_branches:
            try:
                branches = _json.loads(self.allowed_branches) if isinstance(self.allowed_branches, str) else self.allowed_branches
                if branches:
                    # Get student's branch short_name via their academic_batch
                    student_branch = None
                    if student.academic_batch_id:
                        from app.models.academic_batch import AcademicBatch
                        from app.models.branch import Branch
                        batch = db.session.get(AcademicBatch, student.academic_batch_id)
                        if batch and batch.branch_id:
                            branch = db.session.get(Branch, batch.branch_id)
                            student_branch = branch.short_name if branch else None
                    if student_branch and student_branch not in branches:
                        reasons.append(f"Branch '{student_branch}' not in allowed branches: {branches}")
            except Exception:
                pass  # Malformed JSON in allowed_branches — skip check

        # Semesters completed check
        if self.min_semesters_completed is not None:
            if (student.semesters_completed or 0) < self.min_semesters_completed:
                reasons.append(f"Insufficient semesters completed ({self.min_semesters_completed} required, you have {student.semesters_completed or 0})")

        # Verification check — accept tsheet OR admin verification
        from app.models.enums import ProfileStatusEnum
        is_student_verified = (
            student.is_verified or
            student.profile_status == ProfileStatusEnum.VERIFIED
        )
        if not is_student_verified:
            reasons.append("Profile not yet verified. Contact the placement cell.")

        return (len(reasons) == 0, reasons)

    def __repr__(self):
        return f"<EligibilityRule {self.rule_name} policy={self.backlog_policy.value}>"
