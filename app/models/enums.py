"""
Vantage — Shared Enumerations
All enum types used across models, centralised to avoid circular imports.
"""
import enum


# ── User & Auth ─────────────────────────────────────────────────
class RoleEnum(str, enum.Enum):
    VIEWER    = "viewer"     # read-only, unapproved students
    STUDENT   = "student"    # verified student with full profile
    RECRUITER = "recruiter"  # company/recruiter account
    ADMIN     = "admin"      # placement cell staff
    OWNER     = "owner"      # superadmin — billing and destructive actions

    _order = ["viewer", "student", "recruiter", "admin", "owner"]

    def __ge__(self, other: "RoleEnum") -> bool:
        return self._order.index(self.value) >= self._order.index(other.value)

    def __gt__(self, other: "RoleEnum") -> bool:
        return self._order.index(self.value) > self._order.index(other.value)

    def __le__(self, other: "RoleEnum") -> bool:
        return self._order.index(self.value) <= self._order.index(other.value)

    def __lt__(self, other: "RoleEnum") -> bool:
        return self._order.index(self.value) < self._order.index(other.value)


# ── Student Demographics ────────────────────────────────────────
class GenderEnum(enum.Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class CategoryEnum(enum.Enum):
    GENERAL = "GENERAL"
    OBC = "OBC"
    SC = "SC"
    ST = "ST"
    EWS = "EWS"


class ProfileStatusEnum(enum.Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    VERIFIED = "VERIFIED"
    CONFLICT = "CONFLICT"


# ── Academic Batch ──────────────────────────────────────────────
class BatchTypeEnum(enum.Enum):
    REGULAR = "REGULAR"
    LATERAL_ENTRY = "LATERAL_ENTRY"


# ── Course ──────────────────────────────────────────────────────
class CourseTypeEnum(enum.Enum):
    PC = "PC"   # Professional Core
    PE = "PE"   # Professional Elective
    HS = "HS"   # Humanities & Sciences
    BS = "BS"   # Basic Sciences
    ES = "ES"   # Engineering Sciences
    MC = "MC"   # Mandatory Course (0 credit, CIE only)


# ── Examination ─────────────────────────────────────────────────
class ExamTypeEnum(enum.Enum):
    REGULAR = "REGULAR"
    SUPPLEMENTARY = "SUPPLEMENTARY"
    IMPROVEMENT = "IMPROVEMENT"


class GradeLetterEnum(enum.Enum):
    O = "O"
    AP = "A+"
    A = "A"
    BP = "B+"
    B = "B"
    C = "C"
    F = "F"
    AB = "Ab"


# ── T-Sheet Upload ──────────────────────────────────────────────
class UploadStatusEnum(enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class RecordStatusEnum(enum.Enum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    CONFLICT = "CONFLICT"
    DUPLICATE = "DUPLICATE"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


# ── Placement ───────────────────────────────────────────────────
class BacklogPolicyEnum(enum.Enum):
    ACTIVE_ONLY = "ACTIVE_ONLY"
    NEVER_HAD = "NEVER_HAD"


class DriveTypeEnum(enum.Enum):
    FULL_TIME = "FULL_TIME"
    INTERNSHIP = "INTERNSHIP"
    CONTRACT = "CONTRACT"


class DriveStatusEnum(enum.Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ApplicationStatusEnum(enum.Enum):
    APPLIED = "APPLIED"
    SHORTLISTED = "SHORTLISTED"
    WRITTEN_TEST = "WRITTEN_TEST"
    GROUP_DISCUSSION = "GROUP_DISCUSSION"
    TECHNICAL_INTERVIEW = "TECHNICAL_INTERVIEW"
    HR_INTERVIEW = "HR_INTERVIEW"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class MessageTargetEnum(enum.Enum):
    ALL_APPLICANTS = "ALL_APPLICANTS"
    SHORTLISTED = "SHORTLISTED"
    SELECTED = "SELECTED"
