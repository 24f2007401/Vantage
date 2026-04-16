"""
Vantage — Recruiter Routes (Module 7)
All endpoints require @role_required(RoleEnum.RECRUITER).
Privacy: Student names are anonymized unless they have applied to a recruiter's drive.
"""
import io
import csv
import json
from datetime import datetime, timezone, timedelta
import time
from flask import request, jsonify, g, Response, stream_with_context
from sqlalchemy import or_, func

from app.recruiter import recruiter_bp as bp
from app.extensions import db, limiter
from app.utils.decorators import role_required
from app.models.enums import (
    RoleEnum, DriveStatusEnum, DriveTypeEnum,
    ApplicationStatusEnum, ProfileStatusEnum, MessageTargetEnum,
)
from app.models.student import Student
from app.models.placement_drive import PlacementDrive
from app.models.placement_application import PlacementApplication
from app.models.academic_batch import AcademicBatch
from app.models.branch import Branch
from app.models.recruiter import Recruiter
from app.models.eligibility_rule import EligibilityRule
from app.models.drive_message import DriveMessage, StudentMessageDelivery


# ──────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────

def _get_recruiter_id():
    """Get the recruiter_id linked to the current user."""
    return g.current_user.recruiter_id


def _require_fields(data: dict, *fields):
    """Abort 400 if any of the given field names are missing or empty."""
    from flask import abort
    missing = [f for f in fields if not data.get(f)]
    if missing:
        abort(400, description=f"Missing required fields: {', '.join(missing)}")


def _get_own_drive_ids():
    """Return the set of drive IDs belonging to the current recruiter."""
    rid = _get_recruiter_id()
    if not rid:
        return set()
    drives = PlacementDrive.query.filter_by(recruiter_id=rid).all()
    return {d.id for d in drives}


def _has_applied_to_own_drive(student_id):
    """Check if a student has applied to any of the current recruiter's drives."""
    own_drives = _get_own_drive_ids()
    if not own_drives:
        return False
    return PlacementApplication.query.filter(
        PlacementApplication.student_id == student_id,
        PlacementApplication.drive_id.in_(own_drives),
    ).first() is not None


def _anonymize_student(student, reveal_name=False):
    """Return a student dict with privacy controls applied."""
    batch = db.session.get(AcademicBatch, student.academic_batch_id) if student.academic_batch_id else None
    branch = db.session.get(Branch, batch.branch_id) if batch else None

    return {
        "id": student.id,
        "roll_number": student.roll_number,
        "display_name": student.full_name if reveal_name else f"Candidate #{student.roll_number}",
        "branch": branch.short_name if branch else None,
        "graduation_year": batch.graduation_year if batch else None,
        "cgpa": float(student.current_cgpa) if student.current_cgpa else 0.0,
        "backlogs": student.active_backlogs or 0,
        "is_verified": student.profile_status == ProfileStatusEnum.VERIFIED,
        "verified_badge": "Verified via T-Sheet" if student.profile_status == ProfileStatusEnum.VERIFIED else None,
    }


# ──────────────────────────────────────────────────────────────────
#  DASHBOARD
# ──────────────────────────────────────────────────────────────────

@bp.route("/dashboard", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def dashboard():
    """GET /api/recruiter/dashboard — Recruiter's KPIs and active drive summary."""
    rid = _get_recruiter_id()
    if not rid:
        return jsonify({"error": "No recruiter profile linked to your account"}), 404

    recruiter = db.session.get(Recruiter, rid)
    my_drives = PlacementDrive.query.filter_by(recruiter_id=rid).all()

    active_drives = [d for d in my_drives if d.status == DriveStatusEnum.OPEN]
    drive_ids = [d.id for d in my_drives]

    total_applicants = PlacementApplication.query.filter(
        PlacementApplication.drive_id.in_(drive_ids)
    ).count() if drive_ids else 0

    shortlisted = PlacementApplication.query.filter(
        PlacementApplication.drive_id.in_(drive_ids),
        PlacementApplication.status == ApplicationStatusEnum.SHORTLISTED,
    ).count() if drive_ids else 0

    selected = PlacementApplication.query.filter(
        PlacementApplication.drive_id.in_(drive_ids),
        PlacementApplication.status == ApplicationStatusEnum.SELECTED,
    ).count() if drive_ids else 0

    return jsonify({
        "company_name": recruiter.company_name if recruiter else None,
        "active_drives": len(active_drives),
        "total_applicants": total_applicants,
        "shortlisted": shortlisted,
        "selected": selected,
        "my_drives": [
            {
                "id": d.id,
                "company_name": recruiter.company_name if recruiter else None,
                "role_title": d.role_title,
                "applicants_count": PlacementApplication.query.filter_by(drive_id=d.id).count(),
                "status": d.status.value,
            }
            for d in my_drives
        ],
    }), 200


# ──────────────────────────────────────────────────────────────────
#  CANDIDATE BROWSER
# ──────────────────────────────────────────────────────────────────

@bp.route("/candidates", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def list_candidates():
    """GET /api/recruiter/candidates — Browse verified students with filters."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(per_page, 200)

    # Only verified students
    query = Student.query.filter(
        Student.deleted_at.is_(None),
        Student.profile_status == ProfileStatusEnum.VERIFIED,
    )

    # Filters
    branch_codes = request.args.getlist("branch")
    if branch_codes:
        branches = Branch.query.filter(Branch.short_name.in_(branch_codes)).all()
        batch_ids = []
        for b in branches:
            batch_ids.extend([ab.id for ab in AcademicBatch.query.filter_by(branch_id=b.id).all()])
        if batch_ids:
            query = query.filter(Student.academic_batch_id.in_(batch_ids))

    min_cgpa = request.args.get("min_cgpa", type=float)
    if min_cgpa is not None:
        query = query.filter(Student.current_cgpa >= min_cgpa)

    max_backlogs = request.args.get("max_backlogs", type=int)
    if max_backlogs is not None:
        query = query.filter(Student.active_backlogs <= max_backlogs)

    grad_year = request.args.get("graduation_year", type=int)
    if grad_year:
        batch_ids = [b.id for b in AcademicBatch.query.filter_by(graduation_year=grad_year).all()]
        query = query.filter(Student.academic_batch_id.in_(batch_ids))

    pagination = query.order_by(Student.current_cgpa.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Determine which students applied to recruiter's drives
    own_drives = _get_own_drive_ids()
    applied_student_ids = set()
    if own_drives:
        apps = PlacementApplication.query.filter(
            PlacementApplication.drive_id.in_(own_drives)
        ).all()
        applied_student_ids = {a.student_id for a in apps}

    data = []
    for s in pagination.items:
        reveal = s.id in applied_student_ids
        data.append(_anonymize_student(s, reveal_name=reveal))

    return jsonify({
        "items": data,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total,
        "pages": pagination.pages,
    }), 200


@bp.route("/candidates/<student_id>", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def get_candidate(student_id):
    """GET /api/recruiter/candidates/<id> — View a candidate's profile."""
    student = db.session.get(Student, student_id)
    if not student or student.profile_status != ProfileStatusEnum.VERIFIED:
        return jsonify({"error": "Candidate not found"}), 404

    reveal = _has_applied_to_own_drive(student.id)
    result = _anonymize_student(student, reveal_name=reveal)

    # If the student applied to one of recruiter's drives, add full profile
    if reveal:
        result["full_name"] = student.full_name
        result["college_email"] = student.college_email
        result["phone"] = student.phone_primary

    return jsonify(result), 200


# ──────────────────────────────────────────────────────────────────
#  DRIVE MANAGEMENT
# ──────────────────────────────────────────────────────────────────

@bp.route("/drives", methods=["POST"])
@role_required(RoleEnum.RECRUITER)
def create_drive():
    """POST /api/recruiter/drives — Request a new drive (always starts as DRAFT)."""
    rid = _get_recruiter_id()
    if not rid:
        return jsonify({"error": "No recruiter profile linked"}), 404

    data = request.get_json(silent=True) or {}
    _require_fields(data, "drive_title", "role_title", "job_type", "ctc_lpa")

    # Extra metadata not natively on the model goes into additional_criteria JSON
    extra = {
        k: data[k] for k in [
            "openings", "skills", "preferred_certifications",
            "bond", "interview_rounds", "notes", "graduation_years",
        ] if k in data
    }

    drive = PlacementDrive(
        recruiter_id=rid,
        drive_title=data["drive_title"],
        role_title=data["role_title"],
        ctc_lpa=data.get("ctc_lpa"),
        job_location=data.get("job_location"),
        job_type=DriveTypeEnum(data["job_type"]),
        description=data.get("description"),
        status=DriveStatusEnum.DRAFT,
        application_open_at=datetime.fromisoformat(data["application_open_at"]) if data.get("application_open_at") else None,
        application_close_at=datetime.fromisoformat(data["application_close_at"]) if data.get("application_close_at") else None,
    )
    db.session.add(drive)
    db.session.flush()  # Get drive.id before commit

    # Create eligibility rule
    allowed_branches = data.get("allowed_branches", [])
    rule = EligibilityRule(
        drive_id=drive.id,
        rule_name=f"{data['drive_title']} Eligibility",
        min_cgpa=data.get("min_cgpa", 0.0),
        max_active_backlogs=data.get("max_backlogs", 999),
        min_semesters_completed=data.get("min_semesters_completed", 6),
        allowed_branches=json.dumps(allowed_branches) if allowed_branches else None,
        additional_criteria=json.dumps(extra) if extra else None,
        is_active=True,
    )
    db.session.add(rule)
    db.session.commit()

    # Notify all admins of the new drive submission
    try:
        from app.models.notification import Notification
        from app.models.recruiter import Recruiter as _Rec
        rec = db.session.get(_Rec, rid)
        company = rec.company_name if rec else "A recruiter"
        Notification.notify_admins(
            message=f"{company} submitted a new drive: {drive.role_title}",
            notif_type="drive_submitted",
            title="New Drive Submitted",
            sender_id=g.current_user.id,
            link=f"/admin/drives/{drive.id}",
        )
        db.session.commit()
    except Exception:
        pass

    return jsonify({
        "message": "Drive submitted for admin review. You will be notified once approved.",
        "id": drive.id,
        "status": "pending_admin_approval",
    }), 201


@bp.route("/drives", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def list_drives():
    """GET /api/recruiter/drives — List own drives."""
    rid = _get_recruiter_id()
    if not rid:
        return jsonify({"error": "No recruiter profile linked"}), 404

    drives = PlacementDrive.query.filter_by(recruiter_id=rid).order_by(
        PlacementDrive.created_at.desc()
    ).all()

    result = []
    for d in drives:
        result.append({
            "id": d.id,
            "drive_title": d.drive_title,
            "role_title": d.role_title,
            "ctc_lpa": float(d.ctc_lpa) if d.ctc_lpa else None,
            "job_location": d.job_location,
            "job_type": d.job_type.value,
            "status": d.status.value,
            "applicants_count": PlacementApplication.query.filter_by(drive_id=d.id).count(),
            "application_close_at": d.application_close_at.isoformat() if d.application_close_at else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        })
    return jsonify(result), 200


@bp.route("/drives/<drive_id>", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def get_drive(drive_id):
    """GET /api/recruiter/drives/<id> — Single drive detail."""
    rid = _get_recruiter_id()
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "Drive not found"}), 404

    rule = EligibilityRule.query.filter_by(drive_id=drive.id).first()
    extra = {}
    if rule and rule.additional_criteria:
        try:
            extra = json.loads(rule.additional_criteria)
        except Exception:
            pass

    recruiter = db.session.get(Recruiter, rid)
    app_counts = {
        s.value: PlacementApplication.query.filter_by(drive_id=drive.id, status=s).count()
        for s in [ApplicationStatusEnum.APPLIED, ApplicationStatusEnum.SHORTLISTED,
                  ApplicationStatusEnum.SELECTED, ApplicationStatusEnum.REJECTED]
    }

    return jsonify({
        "id": drive.id,
        "drive_title": drive.drive_title,
        "role_title": drive.role_title,
        "company_name": recruiter.company_name if recruiter else None,
        "ctc_lpa": float(drive.ctc_lpa) if drive.ctc_lpa else None,
        "job_location": drive.job_location,
        "job_type": drive.job_type.value,
        "status": drive.status.value,
        "description": drive.description,
        "application_open_at": drive.application_open_at.isoformat() if drive.application_open_at else None,
        "application_close_at": drive.application_close_at.isoformat() if drive.application_close_at else None,
        "created_at": drive.created_at.isoformat() if drive.created_at else None,
        "total_applicants": PlacementApplication.query.filter_by(drive_id=drive.id).count(),
        "status_counts": app_counts,
        "eligibility": {
            "min_cgpa": float(rule.min_cgpa) if rule and rule.min_cgpa else 0.0,
            "max_backlogs": rule.max_active_backlogs if rule else 999,
            "min_semesters_completed": rule.min_semesters_completed if rule else 6,
            "allowed_branches": json.loads(rule.allowed_branches) if rule and rule.allowed_branches else [],
        },
        **extra,
    }), 200


@bp.route("/drives/<drive_id>", methods=["PATCH"])
@role_required(RoleEnum.RECRUITER)
def update_drive(drive_id):
    """PATCH /api/recruiter/drives/<id> — Edit own drive (only while draft)."""
    rid = _get_recruiter_id()
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "Drive not found"}), 404
    if drive.status != DriveStatusEnum.DRAFT:
        return jsonify({"error": "Drive editing is locked once status moves past 'draft'"}), 400

    data = request.get_json(silent=True) or {}
    for field in ["drive_title", "role_title", "job_location", "description"]:
        if field in data:
            setattr(drive, field, data[field])
    if "ctc_lpa" in data:
        drive.ctc_lpa = data["ctc_lpa"]
    if "application_close_at" in data:
        drive.application_close_at = datetime.fromisoformat(data["application_close_at"]) if data["application_close_at"] else None

    db.session.commit()
    return jsonify({"message": "Drive updated"}), 200


# ──────────────────────────────────────────────────────────────────
#  SHORTLISTING
# ──────────────────────────────────────────────────────────────────

@bp.route("/drives/<drive_id>/applicants", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def drive_applicants(drive_id):
    """GET /api/recruiter/drives/<id>/applicants — Applicants with filters."""
    rid = _get_recruiter_id()
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "Drive not found"}), 404

    # Filter params
    branch_filter = request.args.get("branch")
    min_cgpa = request.args.get("min_cgpa", type=float)
    max_backlogs = request.args.get("max_backlogs", type=int)
    grad_year = request.args.get("graduation_year", type=int)
    status_filter = request.args.get("status")
    search = request.args.get("search", "").strip()

    apps = PlacementApplication.query.filter_by(drive_id=drive_id)
    if status_filter:
        try:
            apps = apps.filter(PlacementApplication.status == ApplicationStatusEnum(status_filter.upper()))
        except ValueError:
            pass
    apps = apps.order_by(PlacementApplication.applied_at.desc()).all()

    data = []
    for a in apps:
        student = db.session.get(Student, a.student_id)
        if not student:
            continue
        batch = db.session.get(AcademicBatch, student.academic_batch_id) if student.academic_batch_id else None
        branch = db.session.get(Branch, batch.branch_id) if batch else None

        # Apply in-Python filters (avoids complex joins)
        branch_name = branch.short_name if branch else None
        if branch_filter and branch_name != branch_filter:
            continue
        cgpa = float(student.current_cgpa) if student.current_cgpa else 0.0
        if min_cgpa is not None and cgpa < min_cgpa:
            continue
        backlogs = student.active_backlogs or 0
        if max_backlogs is not None and backlogs > max_backlogs:
            continue
        grad_y = batch.graduation_year if batch else None
        if grad_year and grad_y != grad_year:
            continue
        if search:
            name = (student.full_name or "").lower()
            roll = (student.roll_number or "").lower()
            if search.lower() not in name and search.lower() not in roll:
                continue

        data.append({
            "application_id": a.id,
            "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            "status": a.status.value,
            "offer_ctc": float(a.offer_ctc) if a.offer_ctc else None,
            "candidate": {
                "id": student.id,
                "roll_number": student.roll_number,
                "full_name": student.full_name,
                "branch": branch_name,
                "graduation_year": grad_y,
                "cgpa": cgpa,
                "backlogs": backlogs,
            },
        })

    return jsonify(data), 200


@bp.route("/applications/<app_id>/status", methods=["PATCH"])
@role_required(RoleEnum.RECRUITER)
def update_applicant_status(app_id):
    """PATCH /api/recruiter/applications/<id>/status — Update status for applicant."""
    rid = _get_recruiter_id()
    application = db.session.get(PlacementApplication, app_id)
    if not application:
        return jsonify({"error": "Application not found"}), 404

    # Ownership check
    drive = db.session.get(PlacementDrive, application.drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "You can only update applications for your own drives", "code": 403}), 403

    data = request.get_json(silent=True) or {}
    _require_fields(data, "status")
    new_status_str = data.get("status", "")

    try:
        new_status = ApplicationStatusEnum(new_status_str.upper())
    except ValueError:
        valid = [s.value for s in ApplicationStatusEnum]
        from flask import abort
        abort(422, description=f"Invalid status '{new_status_str}'. Must be one of: {valid}")

    # Append-only status transitions
    status_order = [
        ApplicationStatusEnum.APPLIED,
        ApplicationStatusEnum.WRITTEN_TEST,
        ApplicationStatusEnum.SHORTLISTED,
        ApplicationStatusEnum.GROUP_DISCUSSION,
        ApplicationStatusEnum.TECHNICAL_INTERVIEW,
        ApplicationStatusEnum.HR_INTERVIEW,
        ApplicationStatusEnum.SELECTED,
        ApplicationStatusEnum.REJECTED,
    ]

    if new_status != ApplicationStatusEnum.REJECTED:
        current_idx = status_order.index(application.status) if application.status in status_order else -1
        new_idx = status_order.index(new_status) if new_status in status_order else -1
        if new_idx <= current_idx:
            return jsonify({"error": "Cannot revert to a previous status"}), 400

    application.status = new_status
    if new_status == ApplicationStatusEnum.SELECTED and "offer_ctc" in data:
        application.offer_ctc = data["offer_ctc"]

    db.session.commit()
    return jsonify({"message": f"Application status updated to {new_status.value}"}), 200


@bp.route("/drives/<drive_id>/shortlist", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def drive_shortlist(drive_id):
    """GET /api/recruiter/drives/<id>/shortlist — All shortlisted/selected candidates."""
    rid = _get_recruiter_id()
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "Drive not found"}), 404

    apps = PlacementApplication.query.filter(
        PlacementApplication.drive_id == drive_id,
        PlacementApplication.status.in_([ApplicationStatusEnum.SHORTLISTED, ApplicationStatusEnum.SELECTED]),
    ).all()

    data = []
    for a in apps:
        student = db.session.get(Student, a.student_id)
        batch = db.session.get(AcademicBatch, student.academic_batch_id) if student and student.academic_batch_id else None
        branch = db.session.get(Branch, batch.branch_id) if batch else None

        data.append({
            "application_id": a.id,
            "status": a.status.value,
            "candidate": {
                "roll_number": student.roll_number if student else None,
                "full_name": student.full_name if student else None,
                "branch": branch.short_name if branch else None,
                "cgpa": float(student.current_cgpa) if student and student.current_cgpa else 0.0,
            } if student else None,
            "offer_ctc": float(a.offer_ctc) if a.offer_ctc else None,
        })

    return jsonify(data), 200


@bp.route("/drives/<drive_id>/shortlist/export", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
@limiter.limit("30 per hour")
def export_shortlist(drive_id):
    """GET /api/recruiter/drives/<id>/shortlist/export — CSV export of shortlist."""
    rid = _get_recruiter_id()
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "Drive not found"}), 404

    recruiter = db.session.get(Recruiter, rid)
    apps = PlacementApplication.query.filter(
        PlacementApplication.drive_id == drive_id,
        PlacementApplication.status.in_([ApplicationStatusEnum.SHORTLISTED, ApplicationStatusEnum.SELECTED]),
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Company", "Drive", "Candidate Name", "Roll No", "Branch", "CGPA", "CTC Offered"])

    for a in apps:
        student = db.session.get(Student, a.student_id)
        batch = db.session.get(AcademicBatch, student.academic_batch_id) if student and student.academic_batch_id else None
        branch = db.session.get(Branch, batch.branch_id) if batch else None

        writer.writerow([
            recruiter.company_name if recruiter else "",
            drive.role_title,
            student.full_name if student else "",
            student.roll_number if student else "",
            branch.short_name if branch else "",
            float(student.current_cgpa) if student and student.current_cgpa else 0.0,
            float(a.offer_ctc) if a.offer_ctc else "",
        ])

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    company = recruiter.company_name.replace(" ", "_") if recruiter else "company"
    role = drive.role_title.replace(" ", "_")
    filename = f"{company}_{role}_shortlist_{today}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ──────────────────────────────────────────────────────────────────
#  DRIVE CLOSE
# ──────────────────────────────────────────────────────────────────

@bp.route("/drives/<drive_id>/close", methods=["PATCH"])
@role_required(RoleEnum.RECRUITER)
def close_drive(drive_id):
    """PATCH /api/recruiter/drives/<id>/close — Close an open drive."""
    rid = _get_recruiter_id()
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "Drive not found"}), 404
    drive.status = DriveStatusEnum.CLOSED
    db.session.commit()
    return jsonify({"message": "Drive closed"}), 200


# ──────────────────────────────────────────────────────────────────
#  MESSAGING
# ──────────────────────────────────────────────────────────────────

@bp.route("/candidates/<student_id>/message", methods=["POST"])
@role_required(RoleEnum.RECRUITER)
def message_candidate(student_id):
    """POST /api/recruiter/candidates/<id>/message — Send a message to a candidate."""
    rid = _get_recruiter_id()
    if not _has_applied_to_own_drive(student_id):
        return jsonify({"error": "Candidate has not applied to any of your drives"}), 403

    student = db.session.get(Student, student_id)
    if not student or student.profile_status != ProfileStatusEnum.VERIFIED:
        return jsonify({"error": "Candidate not found"}), 404

    data = request.get_json(silent=True) or {}
    drive_id = data.get("drive_id")
    message_text = data.get("message", "")
    if not message_text:
        return jsonify({"error": "message field is required"}), 400

    # Validate drive ownership
    if drive_id:
        drive = db.session.get(PlacementDrive, drive_id)
        if not drive or drive.recruiter_id != rid:
            drive_id = None

    # Use first own drive if not specified
    if not drive_id:
        own_drives = list(_get_own_drive_ids())
        drive_id = own_drives[0] if own_drives else None

    if not drive_id:
        return jsonify({"error": "No drives found to associate message"}), 400

    msg = DriveMessage(
        drive_id=drive_id,
        sent_by=g.current_user.id,
        message_text=message_text,
        target_audience=MessageTargetEnum.ALL_APPLICANTS,
        delivery_method="PORTAL",
    )
    db.session.add(msg)
    db.session.flush()

    delivery = StudentMessageDelivery(
        message_id=msg.id,
        student_id=student_id,
        is_read=False,
    )
    db.session.add(delivery)
    db.session.commit()
    return jsonify({"message": "Alert sent"}), 200


@bp.route("/drives/<drive_id>/alert", methods=["POST"])
@role_required(RoleEnum.RECRUITER)
def alert_candidates(drive_id):
    """POST /api/recruiter/drives/<id>/alert — Bulk message to selected candidates."""
    rid = _get_recruiter_id()
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive or drive.recruiter_id != rid:
        return jsonify({"error": "Drive not found"}), 404

    data = request.get_json(silent=True) or {}
    candidate_ids = data.get("candidate_ids", [])
    message_text = data.get("message", "")
    if not message_text or not candidate_ids:
        return jsonify({"error": "candidate_ids and message are required"}), 400

    msg = DriveMessage(
        drive_id=drive_id,
        sent_by=g.current_user.id,
        message_text=message_text,
        target_audience=MessageTargetEnum.ALL_APPLICANTS,
        delivery_method="PORTAL",
    )
    db.session.add(msg)
    db.session.flush()

    count = 0
    for sid in candidate_ids:
        student = db.session.get(Student, sid)
        if not student:
            continue
        # Check student applied to this drive
        app = PlacementApplication.query.filter_by(drive_id=drive_id, student_id=sid).first()
        if not app:
            continue
        delivery = StudentMessageDelivery(
            message_id=msg.id,
            student_id=sid,
            is_read=False,
        )
        db.session.add(delivery)
        count += 1

    db.session.commit()

    # Also create Notification rows so portal bell picks it up
    try:
        from app.models.notification import Notification
        from app.models.user import User as _User
        title = data.get("title", "Alert from Recruiter")
        for sid in candidate_ids:
            su = _User.query.filter_by(student_id=sid).first()
            if su:
                Notification.create(
                    recipient_id=su.id,
                    message=message_text,
                    notif_type="student_alert",
                    title=title,
                    sender_id=g.current_user.id,
                    link=f"/student/drives/{drive_id}",
                )
        db.session.commit()
    except Exception:
        pass

    return jsonify({"message": f"Alert sent to {count} candidate(s)"}), 200


# ──────────────────────────────────────────────────────────────────
#  NOTIFICATIONS
# ──────────────────────────────────────────────────────────────────

@bp.route("/notifications", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def get_notifications():
    """GET /api/recruiter/notifications — DB-backed notifications for this recruiter."""
    from app.models.notification import Notification
    rows = Notification.query.filter_by(
        recipient_id=g.current_user.id
    ).order_by(Notification.created_at.desc()).limit(50).all()
    unread = sum(1 for n in rows if not n.is_read)
    return jsonify({
        "notifications": [n.to_dict() for n in rows],
        "unread_count": unread,
    }), 200


@bp.route("/notifications/mark-read", methods=["PATCH"])
@role_required(RoleEnum.RECRUITER)
def mark_notifications_read():
    """PATCH /api/recruiter/notifications/mark-read — Mark notifications as read."""
    from app.models.notification import Notification
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        Notification.query.filter_by(
            recipient_id=g.current_user.id, is_read=False
        ).update({"is_read": True})
    elif data.get("ids"):
        Notification.query.filter(
            Notification.recipient_id == g.current_user.id,
            Notification.id.in_(data["ids"]),
        ).update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True}), 200


@bp.route("/notifications/<notif_id>", methods=["DELETE"])
@role_required(RoleEnum.RECRUITER)
def delete_notification(notif_id):
    """DELETE /api/recruiter/notifications/<id> — Delete own notification."""
    from app.models.notification import Notification
    n = Notification.query.filter_by(id=notif_id, recipient_id=g.current_user.id).first()
    if not n:
        return jsonify({"error": "Notification not found"}), 404
    db.session.delete(n)
    db.session.commit()
    return jsonify({"ok": True}), 200


@bp.route("/message/admin", methods=["POST"])
@role_required(RoleEnum.RECRUITER)
def message_admin():
    """POST /api/recruiter/message/admin — Send a message to all admins."""
    from app.models.notification import Notification
    from app.models.user import User as _User
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    message = (data.get("message") or "").strip()
    drive_id = data.get("drive_id")

    if not message:
        return jsonify({"error": "message is required"}), 400

    link = f"/admin/drives/{drive_id}" if drive_id else None
    Notification.notify_admins(
        message=message,
        notif_type="admin_message",
        title=title or "Message from Recruiter",
        sender_id=g.current_user.id,
        link=link,
    )
    db.session.commit()
    return jsonify({"ok": True, "message": "Message sent to admins"}), 201


# ──────────────────────────────────────────────────────────────────
#  SEARCH
# ──────────────────────────────────────────────────────────────────

@bp.route("/search", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def recruiter_search():
    """GET /api/recruiter/search?q=<term>&types=candidates,drives"""
    rid = _get_recruiter_id()
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"candidates": [], "drives": []}), 200

    types_param = request.args.get("types", "candidates,drives")
    types = {t.strip() for t in types_param.split(",")}
    result = {}
    like = f"%{q}%"

    if "drives" in types:
        rows = PlacementDrive.query.filter(
            PlacementDrive.recruiter_id == rid,
            or_(
                PlacementDrive.drive_title.ilike(like),
                PlacementDrive.role_title.ilike(like),
            ),
        ).limit(5).all()
        result["drives"] = [
            {
                "id": d.id,
                "job_title": d.role_title,
                "company": d.drive_title,
                "status": d.status.value,
            }
            for d in rows
        ]

    if "candidates" in types and rid:
        own_drives = {d.id for d in PlacementDrive.query.filter_by(recruiter_id=rid).all()}
        if own_drives:
            applied_ids = {
                a.student_id for a in PlacementApplication.query.filter(
                    PlacementApplication.drive_id.in_(own_drives)
                ).all()
            }
            rows = Student.query.filter(
                Student.id.in_(applied_ids),
                or_(
                    Student.full_name.ilike(like),
                    Student.roll_number.ilike(like),
                ),
            ).limit(5).all()
            result["candidates"] = [
                {
                    "id": s.id,
                    "roll_number": s.roll_number,
                    "name": s.full_name,
                    "cgpa": float(s.current_cgpa) if s.current_cgpa else 0.0,
                }
                for s in rows
            ]
        else:
            result["candidates"] = []

    return jsonify(result), 200


# ──────────────────────────────────────────────────────────────────
#  BRANCHES (public-enough for recruiter drive creation)
# ──────────────────────────────────────────────────────────────────

@bp.route("/branches", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def list_branches():
    """GET /api/recruiter/branches — All active branches for drive creation form."""
    branches = Branch.query.filter(Branch.is_active != False).order_by(Branch.short_name).all()
    return jsonify([
        {"id": b.id, "short_name": b.short_name or b.branch_code, "name": b.name or b.short_name}
        for b in branches
    ]), 200


# ──────────────────────────────────────────────────────────────────
#  SSE — RECRUITER EVENTS
# ──────────────────────────────────────────────────────────────────

def _verify_recruiter_token(token: str):
    """Verify a raw JWT string for SSE ?token= query param. Returns (recruiter_id, user_id) or (None, None)."""
    if not token:
        return None, None
    try:
        from flask_jwt_extended import decode_token
        from app.models.user import User as _User
        from app.extensions import db as _db
        decoded = decode_token(token)
        user_id = decoded.get("sub")
        user = _db.session.get(_User, user_id)
        if user and user.is_active and user.role == RoleEnum.RECRUITER:
            return user.recruiter_id, user.id
        return None, None
    except Exception:
        return None, None


@bp.route("/events", methods=["GET"])
def recruiter_events():
    """
    GET /api/recruiter/events?token=<jwt>
    SSE stream — emits drive status changes and new applications every 5s.
    """
    rid, recruiter_user_id = _verify_recruiter_token(request.args.get("token"))
    if rid is None:
        return jsonify({"error": "Unauthorized"}), 401

    def generate():
        last_app_count = None
        last_drive_statuses = {}
        last_unread_notif_count = None

        while True:
            try:
                db.session.expire_all()

                events = []

                # Unread notifications
                from app.models.notification import Notification as _Notif
                cur_unread = _Notif.query.filter_by(
                    recipient_id=recruiter_user_id, is_read=False
                ).count()
                if last_unread_notif_count is not None and cur_unread > last_unread_notif_count:
                    events.append({
                        "type": "notification_created",
                        "data": {"unread_count": cur_unread},
                    })
                last_unread_notif_count = cur_unread

                if rid:
                    drives = PlacementDrive.query.filter_by(recruiter_id=rid).all()

                    # Detect drive status changes
                    for d in drives:
                        prev = last_drive_statuses.get(d.id)
                        if prev is not None and prev != d.status.value:
                            events.append({
                                "type": "drive_updated",
                                "entity_id": d.id,
                                "data": {"status": d.status.value, "drive_title": d.drive_title},
                            })
                        last_drive_statuses[d.id] = d.status.value

                    # Detect new applications
                    drive_ids = [d.id for d in drives]
                    cur_count = (
                        PlacementApplication.query.filter(
                            PlacementApplication.drive_id.in_(drive_ids)
                        ).count()
                        if drive_ids else 0
                    )
                    if last_app_count is not None and cur_count > last_app_count:
                        events.append({
                            "type": "application_created",
                            "entity_id": None,
                            "data": {"new_applications": cur_count - last_app_count, "total": cur_count},
                        })
                    last_app_count = cur_count

                for ev in events:
                    yield f"data: {json.dumps(ev)}\n\n"

                # Heartbeat every cycle
                yield f": heartbeat\n\n"

            except Exception:
                pass

            time.sleep(5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────────────────────────────
#  ANALYTICS
# ──────────────────────────────────────────────────────────────────

@bp.route("/analytics/by-branch", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def analytics_by_branch():
    """GET /api/recruiter/analytics/by-branch — Branch distribution of own applicants."""
    rid = _get_recruiter_id()
    if not rid:
        return jsonify([]), 200

    drive_ids = [d.id for d in PlacementDrive.query.filter_by(recruiter_id=rid).all()]
    if not drive_ids:
        return jsonify([]), 200

    apps = PlacementApplication.query.filter(
        PlacementApplication.drive_id.in_(drive_ids)
    ).all()

    branch_counts = {}
    for a in apps:
        student = db.session.get(Student, a.student_id)
        if not student:
            continue
        batch = db.session.get(AcademicBatch, student.academic_batch_id) if student.academic_batch_id else None
        branch = db.session.get(Branch, batch.branch_id) if batch else None
        name = branch.short_name if branch else "Unknown"
        branch_counts[name] = branch_counts.get(name, 0) + 1

    return jsonify([
        {"branch": k, "count": v}
        for k, v in sorted(branch_counts.items(), key=lambda x: -x[1])
    ]), 200


@bp.route("/analytics/cgpa-distribution", methods=["GET"])
@role_required(RoleEnum.RECRUITER)
def analytics_cgpa_distribution():
    """GET /api/recruiter/analytics/cgpa-distribution — CGPA histogram of applicants."""
    rid = _get_recruiter_id()
    if not rid:
        return jsonify([]), 200

    drive_ids = [d.id for d in PlacementDrive.query.filter_by(recruiter_id=rid).all()]
    if not drive_ids:
        return jsonify([]), 200

    apps = PlacementApplication.query.filter(
        PlacementApplication.drive_id.in_(drive_ids)
    ).all()

    buckets = {}
    ranges = ["5.0-6.0", "6.0-7.0", "7.0-7.5", "7.5-8.0", "8.0-8.5", "8.5-9.0", "9.0-10.0"]
    for r in ranges:
        buckets[r] = 0

    for a in apps:
        student = db.session.get(Student, a.student_id)
        if not student or not student.current_cgpa:
            continue
        cgpa = float(student.current_cgpa)
        if cgpa < 5.0:
            pass
        elif cgpa < 6.0:
            buckets["5.0-6.0"] += 1
        elif cgpa < 7.0:
            buckets["6.0-7.0"] += 1
        elif cgpa < 7.5:
            buckets["7.0-7.5"] += 1
        elif cgpa < 8.0:
            buckets["7.5-8.0"] += 1
        elif cgpa < 8.5:
            buckets["8.0-8.5"] += 1
        elif cgpa < 9.0:
            buckets["8.5-9.0"] += 1
        else:
            buckets["9.0-10.0"] += 1

    return jsonify([{"range": k, "count": v} for k, v in buckets.items()]), 200


@bp.route("/notify-admin", methods=["POST"])
@role_required(RoleEnum.RECRUITER)
def notify_admin():
    """POST /api/recruiter/notify-admin — Send a message to all admin users."""
    from app.models.user import User

    data = request.get_json(silent=True) or {}
    subject = (data.get("subject") or "").strip()
    message_text = (data.get("message") or "").strip()
    drive_id = data.get("drive_id")

    if not subject or not message_text:
        return jsonify({"error": "subject and message are required"}), 400

    rid = _get_recruiter_id()

    # If drive_id provided, validate ownership
    if drive_id:
        drive = db.session.get(PlacementDrive, drive_id)
        if not drive or drive.recruiter_id != rid:
            return jsonify({"error": "Drive not found or not yours"}), 403
    else:
        # Use first own drive as association, or create without drive
        own_drive_ids = list(_get_own_drive_ids())
        drive_id = own_drive_ids[0] if own_drive_ids else None

    if not drive_id:
        return jsonify({"error": "No drives found to associate this notification"}), 400

    full_text = f"[{subject}] {message_text}"
    msg = DriveMessage(
        drive_id=drive_id,
        sent_by=g.current_user.id,
        message_text=full_text,
        target_audience=MessageTargetEnum.ALL_APPLICANTS,
        delivery_method="PORTAL",
    )
    db.session.add(msg)
    db.session.flush()

    # Deliver to all admin users
    admins = User.query.filter(User.role.in_([RoleEnum.ADMIN, RoleEnum.OWNER])).all()
    for admin in admins:
        if admin.student_id:
            delivery = StudentMessageDelivery(
                message_id=msg.id,
                student_id=admin.student_id,
                is_read=False,
            )
            db.session.add(delivery)

    db.session.commit()
    return jsonify({"message": "Admin notified", "admins_notified": len(admins)}), 200
