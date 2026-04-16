"""
Vantage — Student Routes (Module 5)
All endpoints require @role_required(RoleEnum.STUDENT).
"""
import json
import time
from datetime import datetime, timezone, date
from flask import request, jsonify, g, Response, stream_with_context
from app.student import student_bp as bp
from app.extensions import db
from app.utils.decorators import role_required, verified_required
from app.models.enums import (
    RoleEnum, DriveStatusEnum, ApplicationStatusEnum, ProfileStatusEnum,
)
from app.models.student import Student
from app.models.placement_drive import PlacementDrive
from app.models.placement_application import PlacementApplication
from app.models.eligibility_rule import EligibilityRule
from app.models.exam_result import ExamResult
from app.models.student_semester_summary import StudentSemesterSummary
from app.models.academic_batch import AcademicBatch
from app.models.branch import Branch
from app.models.certification import StudentCertEntry

ALLOWED_CERT_PROVIDERS = [
    "AWS (Amazon Web Services)",
    "Google Cloud",
    "Microsoft Azure",
    "Oracle",
    "Cisco",
    "CompTIA",
    "Meta (Facebook) Developer",
    "Salesforce",
    "Red Hat",
    "Linux Foundation",
    "PMI",
    "Scrum Alliance / Scrum.org",
    "GitHub Certifications",
    "Databricks",
    "Snowflake",
    "MongoDB University",
    "HashiCorp",
    "CFA Institute",
    "NASSCOM",
]


# ──────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────

def _get_student_or_404():
    """Resolve the Student record linked to the authenticated user."""
    user = g.current_user
    if not user.student_id:
        return None
    return db.session.get(Student, user.student_id)


def _drive_to_dict(drive, student=None):
    """Serialize a PlacementDrive, optionally including eligibility for a student."""
    data = {
        "id": drive.id,
        "drive_title": drive.drive_title,
        "role_title": drive.role_title,
        "ctc_lpa": float(drive.ctc_lpa) if drive.ctc_lpa else None,
        "job_location": drive.job_location,
        "application_deadline": drive.application_close_at.isoformat() if drive.application_close_at else None,
        "status": drive.status.value,
        "description": drive.description,
        "created_at": drive.created_at.isoformat() if drive.created_at else None,
    }

    if student:
        rules = EligibilityRule.query.filter_by(drive_id=drive.id, is_active=True).all()
        all_reasons = []
        is_eligible = True
        for rule in rules:
            eligible, reasons = rule.evaluate(student)
            if not eligible:
                is_eligible = False
                all_reasons.extend(reasons)
        # If no rules exist, student is eligible by default
        data["eligibility"] = {
            "is_eligible": is_eligible,
            "reasons": all_reasons,
        }

    return data


def _application_to_dict(app):
    """Serialize a PlacementApplication with drive summary."""
    drive = db.session.get(PlacementDrive, app.drive_id)
    return {
        "id": app.id,
        "applied_at": app.applied_at.isoformat() if app.applied_at else None,
        "status": app.status.value,
        "offer_ctc": float(app.offer_ctc) if app.offer_ctc else None,
        "drive": {
            "id": drive.id if drive else None,
            "drive_title": drive.drive_title if drive else None,
            "role_title": drive.role_title if drive else None,
            "ctc_lpa": float(drive.ctc_lpa) if drive and drive.ctc_lpa else None,
            "status": drive.status.value if drive else None,
        } if drive else None,
    }


# ──────────────────────────────────────────────────────────────────
#  PROFILE
# ──────────────────────────────────────────────────────────────────

@bp.route("/profile", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def get_profile():
    """GET /api/student/profile — Return the student's own verified profile."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked to your account", "code": 404}), 404

    # Fetch branch info
    batch = db.session.get(AcademicBatch, student.academic_batch_id) if student.academic_batch_id else None
    branch = db.session.get(Branch, batch.branch_id) if batch else None

    # Fetch semester summaries
    summaries = StudentSemesterSummary.query.filter_by(student_id=student.id).order_by(
        StudentSemesterSummary.semester_number.asc()
    ).all()

    academic_records = []
    for s in summaries:
        academic_records.append({
            "semester": s.semester_number,
            "sgpa": float(s.sgpa) if s.sgpa else None,
            "backlogs": s.backlogs_this_semester,
            "credits_earned": float(s.total_credits_earned) if s.total_credits_earned else None,
        })

    # Determine verification badge
    is_verified = student.is_verified or student.profile_status == ProfileStatusEnum.VERIFIED
    verification_source = student.verification_source
    if student.profile_status == ProfileStatusEnum.VERIFIED and not verification_source:
        verification_source = "tsheet"

    return jsonify({
        "roll_number": student.roll_number,
        "full_name": student.full_name,
        "branch": branch.short_name if branch else None,
        "graduation_year": batch.graduation_year if batch else None,
        "cgpa": float(student.current_cgpa) if student.current_cgpa else 0.0,
        "backlogs": student.active_backlogs or 0,
        "total_backlogs": student.total_backlogs_ever or 0,
        "is_verified": is_verified,
        "verification_source": verification_source,
        "verified_at": student.verified_at.isoformat() if student.verified_at else None,
        "phone": student.phone_primary,
        "dob": student.date_of_birth.isoformat() if student.date_of_birth else None,
        "gender": student.gender.value if student.gender else None,
        "personal_email": student.personal_email,
        "address": student.address_local,
        "github_url": student.github_url,
        "linkedin_url": student.linkedin_url,
        "portfolio_url": student.portfolio_url,
        "resume_url": student.resume_url,
        "skills": json.loads(student.skills_json) if student.skills_json else [],
        "academic_records": academic_records,
    }), 200


@bp.route("/profile", methods=["PATCH"])
@role_required(RoleEnum.STUDENT)
def update_profile():
    """PATCH /api/student/profile — Update non-academic fields only."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked to your account", "code": 404}), 404

    data = request.get_json(silent=True) or {}

    # Block protected academic/identity fields
    protected_fields = {
        "cgpa", "backlogs", "is_verified", "current_cgpa", "active_backlogs",
        "roll_number", "branch", "graduation_year", "profile_status",
        "verification_source", "total_backlogs_ever", "semesters_completed",
    }
    for field in protected_fields:
        if field in data:
            return jsonify({
                "error": "Academic data is managed by the T-Sheet pipeline.",
                "code": 403,
            }), 403

    # Apply allowed editable fields
    if "full_name" in data:
        student.full_name = (data["full_name"] or "").strip() or None
    if "phone" in data:
        student.phone_primary = (data["phone"] or "").strip() or None
    if "dob" in data:
        try:
            student.date_of_birth = date.fromisoformat(data["dob"]) if data["dob"] else None
        except ValueError:
            return jsonify({"error": "Invalid date format for dob (use YYYY-MM-DD)"}), 400
    if "gender" in data:
        from app.models.enums import GenderEnum
        allowed_genders = {g.value for g in GenderEnum}
        val = data["gender"]
        if val and val not in allowed_genders:
            return jsonify({"error": f"gender must be one of {sorted(allowed_genders)}"}), 400
        student.gender = GenderEnum(val) if val else None
    if "personal_email" in data:
        student.personal_email = (data["personal_email"] or "").strip() or None
    if "address" in data:
        student.address_local = data["address"] or None
    if "github_url" in data:
        url = (data["github_url"] or "").strip()
        if url and "github.com" not in url:
            return jsonify({"error": "github_url must contain github.com"}), 400
        student.github_url = url or None
    if "linkedin_url" in data:
        url = (data["linkedin_url"] or "").strip()
        if url and "linkedin.com" not in url:
            return jsonify({"error": "linkedin_url must contain linkedin.com"}), 400
        student.linkedin_url = url or None
    if "portfolio_url" in data:
        student.portfolio_url = (data["portfolio_url"] or "").strip() or None
    if "resume_url" in data:
        student.resume_url = (data["resume_url"] or "").strip() or None
    if "skills" in data:
        skills = data["skills"]
        if not isinstance(skills, list):
            return jsonify({"error": "skills must be a list of strings"}), 400
        student.skills_json = json.dumps([str(s).strip() for s in skills if str(s).strip()])

    db.session.commit()
    return jsonify({"message": "Profile updated successfully"}), 200


# ──────────────────────────────────────────────────────────────────
#  DRIVES
# ──────────────────────────────────────────────────────────────────

@bp.route("/drives", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def list_drives():
    """GET /api/student/drives — List open drives with eligibility status."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    now = datetime.now(timezone.utc)
    drives = PlacementDrive.query.filter(
        PlacementDrive.status == DriveStatusEnum.OPEN,
        (PlacementDrive.application_close_at.is_(None)) | (PlacementDrive.application_close_at >= now),
    ).order_by(PlacementDrive.created_at.desc()).all()

    return jsonify([_drive_to_dict(d, student) for d in drives]), 200


@bp.route("/drives/<drive_id>", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def get_drive(drive_id):
    """GET /api/student/drives/<id> — Single drive detail + eligibility."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found", "code": 404}), 404

    return jsonify(_drive_to_dict(drive, student)), 200


# ──────────────────────────────────────────────────────────────────
#  APPLICATIONS
# ──────────────────────────────────────────────────────────────────

@bp.route("/drives/<drive_id>/apply", methods=["POST"])
@role_required(RoleEnum.STUDENT)
def apply_to_drive(drive_id):
    """POST /api/student/drives/<id>/apply — Submit an application."""
    # 1. Student profile must exist
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked to your account.", "code": 404}), 404

    # 2. Verification check — tsheet or admin verified
    is_verified = student.is_verified or student.profile_status == ProfileStatusEnum.VERIFIED
    if not is_verified:
        return jsonify({"error": "Your profile is not verified yet. Contact the placement cell.", "code": 403}), 403

    # 3. Drive must exist and status = open
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found.", "code": 404}), 404
    if drive.status != DriveStatusEnum.OPEN:
        return jsonify({"error": "Drive is not open for applications.", "code": 400}), 400

    # 4. Deadline must not have passed
    now = datetime.now(timezone.utc)
    if drive.application_close_at:
        deadline = drive.application_close_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline < now:
            return jsonify({"error": "Application deadline has passed.", "code": 400}), 400

    # 5. Duplicate check
    existing = PlacementApplication.query.filter_by(
        student_id=student.id, drive_id=drive.id
    ).first()
    if existing:
        return jsonify({"error": "You have already applied to this drive.", "code": 409}), 409

    # 6. Eligibility check
    rules = EligibilityRule.query.filter_by(drive_id=drive.id, is_active=True).all()
    all_reasons = []
    for rule in rules:
        eligible, reasons = rule.evaluate(student)
        if not eligible:
            all_reasons.extend(reasons)

    if all_reasons:
        return jsonify({
            "error": "You are not eligible for this drive.",
            "code": 403,
            "reasons": all_reasons,
        }), 403

    # Create application
    application = PlacementApplication(
        student_id=student.id,
        drive_id=drive.id,
        status=ApplicationStatusEnum.APPLIED,
    )
    db.session.add(application)
    db.session.commit()

    return jsonify({
        "message": "Application submitted successfully",
        "application_id": application.id,
    }), 201


@bp.route("/applications", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def list_applications():
    """GET /api/student/applications — List all own applications."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    apps = PlacementApplication.query.filter_by(
        student_id=student.id
    ).order_by(PlacementApplication.applied_at.desc()).all()

    return jsonify([_application_to_dict(a) for a in apps]), 200


@bp.route("/applications/<app_id>", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def get_application(app_id):
    """GET /api/student/applications/<id> — Single application detail."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    app = PlacementApplication.query.filter_by(
        id=app_id, student_id=student.id
    ).first()
    if not app:
        return jsonify({"error": "Application not found", "code": 404}), 404

    return jsonify(_application_to_dict(app)), 200


# ──────────────────────────────────────────────────────────────────
#  DASHBOARD
# ──────────────────────────────────────────────────────────────────

@bp.route("/dashboard", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def dashboard():
    """GET /api/student/dashboard — Aggregated KPIs for dashboard render."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    # Count applications
    applications_count = PlacementApplication.query.filter_by(student_id=student.id).count()
    offers_count = PlacementApplication.query.filter_by(
        student_id=student.id, status=ApplicationStatusEnum.SELECTED
    ).count()

    # Count eligible open drives
    now = datetime.now(timezone.utc)
    open_drives = PlacementDrive.query.filter(
        PlacementDrive.status == DriveStatusEnum.OPEN,
        (PlacementDrive.application_close_at.is_(None)) | (PlacementDrive.application_close_at >= now),
    ).all()

    eligible_count = 0
    for drive in open_drives:
        rules = EligibilityRule.query.filter_by(drive_id=drive.id, is_active=True).all()
        is_eligible = True
        for rule in rules:
            ok, _ = rule.evaluate(student)
            if not ok:
                is_eligible = False
                break
        if is_eligible:
            eligible_count += 1

    is_verified = student.is_verified or student.profile_status == ProfileStatusEnum.VERIFIED
    verification_source = student.verification_source
    if is_verified and not verification_source:
        verification_source = "tsheet"
    return jsonify({
        "cgpa": float(student.current_cgpa) if student.current_cgpa else 0.0,
        "backlogs": student.active_backlogs or 0,
        "is_verified": is_verified,
        "verification_source": verification_source,
        "applications_count": applications_count,
        "offers_count": offers_count,
        "open_drives_eligible": eligible_count,
    }), 200


# ──────────────────────────────────────────────────────────────────
#  CERTIFICATIONS
# ──────────────────────────────────────────────────────────────────

@bp.route("/certifications", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def list_certifications():
    """GET /api/student/certifications — List own certification entries."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    entries = StudentCertEntry.query.filter_by(student_id=student.id).order_by(
        StudentCertEntry.created_at.desc()
    ).all()

    return jsonify([{
        "id": e.id,
        "provider": e.provider,
        "cert_name": e.cert_name,
        "credential_id": e.credential_id,
        "issue_date": e.issue_date.isoformat() if e.issue_date else None,
        "expiry_date": e.expiry_date.isoformat() if e.expiry_date else None,
        "verify_url": e.verify_url,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in entries]), 200


@bp.route("/certifications", methods=["POST"])
@role_required(RoleEnum.STUDENT)
def add_certification():
    """POST /api/student/certifications — Add a new certification entry."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip()
    cert_name = (data.get("cert_name") or "").strip()

    if not provider:
        return jsonify({"error": "provider is required"}), 400
    if not cert_name:
        return jsonify({"error": "cert_name is required"}), 400

    # Validate provider
    if provider not in ALLOWED_CERT_PROVIDERS and not provider.startswith("Other"):
        return jsonify({
            "error": "Certification provider not in approved list",
            "allowed_providers": ALLOWED_CERT_PROVIDERS,
        }), 400

    # Parse dates
    issue_date = None
    expiry_date = None
    try:
        if data.get("issue_date"):
            issue_date = date.fromisoformat(data["issue_date"])
        if data.get("expiry_date"):
            expiry_date = date.fromisoformat(data["expiry_date"])
    except ValueError:
        return jsonify({"error": "Invalid date format (use YYYY-MM-DD)"}), 400

    entry = StudentCertEntry(
        student_id=student.id,
        provider=provider,
        cert_name=cert_name,
        credential_id=(data.get("credential_id") or "").strip() or None,
        issue_date=issue_date,
        expiry_date=expiry_date,
        verify_url=(data.get("verify_url") or "").strip() or None,
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify({
        "message": "Certification added",
        "id": entry.id,
    }), 201


@bp.route("/certifications/<entry_id>", methods=["DELETE"])
@role_required(RoleEnum.STUDENT)
def delete_certification(entry_id):
    """DELETE /api/student/certifications/<id> — Remove own certification."""
    student = _get_student_or_404()
    if not student:
        return jsonify({"error": "No student profile linked", "code": 404}), 404

    entry = StudentCertEntry.query.filter_by(id=entry_id, student_id=student.id).first()
    if not entry:
        return jsonify({"error": "Certification not found"}), 404

    db.session.delete(entry)
    db.session.commit()
    return jsonify({"message": "Certification removed"}), 200


# ──────────────────────────────────────────────────────────────────
#  NOTIFICATIONS
# ──────────────────────────────────────────────────────────────────

@bp.route("/notifications", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def get_notifications():
    """GET /api/student/notifications — DB-backed notifications for this student."""
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
@role_required(RoleEnum.STUDENT)
def mark_notifications_read():
    """PATCH /api/student/notifications/mark-read — Mark notifications as read."""
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
@role_required(RoleEnum.STUDENT)
def delete_notification(notif_id):
    """DELETE /api/student/notifications/<id> — Delete own notification."""
    from app.models.notification import Notification
    n = Notification.query.filter_by(id=notif_id, recipient_id=g.current_user.id).first()
    if not n:
        return jsonify({"error": "Notification not found"}), 404
    db.session.delete(n)
    db.session.commit()
    return jsonify({"ok": True}), 200


# ──────────────────────────────────────────────────────────────────
#  SEARCH
# ──────────────────────────────────────────────────────────────────

@bp.route("/search", methods=["GET"])
@role_required(RoleEnum.STUDENT)
def student_search():
    """GET /api/student/search?q=<term> — Search drives visible to this student."""
    student = _get_student_or_404()
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"drives": []}), 200

    from sqlalchemy import or_
    like = f"%{q}%"
    drives = PlacementDrive.query.filter(
        PlacementDrive.status == DriveStatusEnum.OPEN,
        or_(
            PlacementDrive.drive_title.ilike(like),
            PlacementDrive.role_title.ilike(like),
        ),
    ).limit(5).all()

    result = []
    for d in drives:
        is_eligible = True
        if student:
            rules = EligibilityRule.query.filter_by(drive_id=d.id, is_active=True).all()
            for rule in rules:
                ok, _ = rule.evaluate(student)
                if not ok:
                    is_eligible = False
                    break
        result.append({
            "id": d.id,
            "job_title": d.role_title,
            "company": d.drive_title,
            "status": d.status.value,
            "is_eligible": is_eligible,
        })

    return jsonify({"drives": result}), 200


# ──────────────────────────────────────────────────────────────────
#  SSE — STUDENT EVENTS
# ──────────────────────────────────────────────────────────────────

def _verify_student_token(token: str):
    """Verify a raw JWT string for SSE ?token= query param. Returns User or None."""
    if not token:
        return None
    try:
        from flask_jwt_extended import decode_token
        from app.models.user import User as _User
        from app.extensions import db as _db
        decoded = decode_token(token)
        user_id = decoded.get("sub")
        user = _db.session.get(_User, user_id)
        if user and user.is_active and user.role == RoleEnum.STUDENT:
            return user
        return None
    except Exception:
        return None


@bp.route("/events", methods=["GET"])
def student_events():
    """
    GET /api/student/events?token=<jwt>
    SSE stream — emits verification changes, new drives, application updates.
    """
    user = _verify_student_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    student_id = user.student_id

    student_user_id = user.id

    def generate():
        last_is_verified = None
        last_open_drives = None
        last_app_statuses = {}
        last_unread_notif_count = None

        while True:
            try:
                db.session.expire_all()
                events = []

                # Unread notifications
                from app.models.notification import Notification as _Notif
                cur_unread = _Notif.query.filter_by(
                    recipient_id=student_user_id, is_read=False
                ).count()
                if last_unread_notif_count is not None and cur_unread > last_unread_notif_count:
                    events.append({
                        "type": "notification_created",
                        "data": {"unread_count": cur_unread},
                    })
                last_unread_notif_count = cur_unread

                if student_id:
                    student = db.session.get(Student, student_id)
                    if student:
                        # Verification change
                        cur_verified = student.is_verified
                        if last_is_verified is not None and cur_verified != last_is_verified:
                            events.append({
                                "type": "verification_updated",
                                "entity_id": student_id,
                                "data": {
                                    "is_verified": cur_verified,
                                    "verification_source": student.verification_source,
                                },
                            })
                        last_is_verified = cur_verified

                        # Application status changes
                        apps = PlacementApplication.query.filter_by(student_id=student_id).all()
                        for a in apps:
                            prev = last_app_statuses.get(a.id)
                            if prev is not None and prev != a.status.value:
                                events.append({
                                    "type": "application_updated",
                                    "entity_id": a.id,
                                    "data": {"status": a.status.value, "drive_id": a.drive_id},
                                })
                            last_app_statuses[a.id] = a.status.value

                # New open drives
                now = datetime.now(timezone.utc)
                cur_open = PlacementDrive.query.filter(
                    PlacementDrive.status == DriveStatusEnum.OPEN,
                    (PlacementDrive.application_close_at.is_(None)) |
                    (PlacementDrive.application_close_at >= now),
                ).count()
                if last_open_drives is not None and cur_open > last_open_drives:
                    events.append({
                        "type": "drive_created",
                        "entity_id": None,
                        "data": {"open_drives_count": cur_open},
                    })
                last_open_drives = cur_open

                for ev in events:
                    yield f"data: {json.dumps(ev)}\n\n"

                yield f": heartbeat\n\n"

            except Exception:
                pass

            time.sleep(5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
