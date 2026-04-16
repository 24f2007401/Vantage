"""
Vantage — Models Package
Re-exports all models and enums for convenient imports.
"""
# ── Enums ───────────────────────────────────────────────────────
from app.models.enums import (
    RoleEnum, GenderEnum, CategoryEnum, ProfileStatusEnum,
    BatchTypeEnum, CourseTypeEnum, ExamTypeEnum, GradeLetterEnum,
    UploadStatusEnum, RecordStatusEnum, BacklogPolicyEnum,
    DriveTypeEnum, DriveStatusEnum, ApplicationStatusEnum,
    MessageTargetEnum,
)

# ── Institution Hierarchy ───────────────────────────────────────
from app.models.institution import Institution
from app.models.department import Department
from app.models.branch import Branch
from app.models.academic_batch import AcademicBatch

# ── Core Academic ───────────────────────────────────────────────
from app.models.student import Student
from app.models.course import Course
from app.models.exam_batch import ExamBatch
from app.models.exam_result import ExamResult
from app.models.student_semester_summary import StudentSemesterSummary

# ── T-Sheet Pipeline ───────────────────────────────────────────
from app.models.tsheet_upload import TSheetUpload
from app.models.tsheet_record import TSheetRecord

# ── Certifications ──────────────────────────────────────────────
from app.models.certification import Certification, StudentCertification

# ── Auth & Users ────────────────────────────────────────────────
from app.models.user import User
from app.models.recruiter import Recruiter

# ── Placement ───────────────────────────────────────────────────
from app.models.placement_drive import PlacementDrive
from app.models.eligibility_rule import EligibilityRule
from app.models.placement_application import PlacementApplication

# ── Messaging ───────────────────────────────────────────────────
from app.models.drive_message import DriveMessage, StudentMessageDelivery

# ── Notifications ───────────────────────────────────────────────
from app.models.notification import Notification

__all__ = [
    # Enums
    "RoleEnum", "GenderEnum", "CategoryEnum", "ProfileStatusEnum",
    "BatchTypeEnum", "CourseTypeEnum", "ExamTypeEnum", "GradeLetterEnum",
    "UploadStatusEnum", "RecordStatusEnum", "BacklogPolicyEnum",
    "DriveTypeEnum", "DriveStatusEnum", "ApplicationStatusEnum",
    "MessageTargetEnum",
    # Models
    "Institution", "Department", "Branch", "AcademicBatch",
    "Student", "Course", "ExamBatch", "ExamResult",
    "StudentSemesterSummary",
    "TSheetUpload", "TSheetRecord",
    "Certification", "StudentCertification",
    "User", "Recruiter",
    "PlacementDrive", "EligibilityRule", "PlacementApplication",
    "DriveMessage", "StudentMessageDelivery",
    "Notification",
]
