"""
Vantage — Admin Routes (Module 4 T-Sheet + Module 6 Admin API)
"""
import io
import csv
import json
import time
import threading
import hashlib
from datetime import datetime, timezone
from flask import request, jsonify, g, Response, stream_with_context, current_app
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.admin import bp
from app.extensions import db, limiter
from app.utils.decorators import role_required
from app.models.user import User, RoleEnum
from app.models.enums import (
    UploadStatusEnum, RecordStatusEnum, ExamTypeEnum,
    DriveStatusEnum, DriveTypeEnum, ApplicationStatusEnum,
    ProfileStatusEnum, GradeLetterEnum, BacklogPolicyEnum,
)
from app.models.student import Student
from app.models.tsheet_upload import TSheetUpload
from app.models.tsheet_record import TSheetRecord
from app.models.exam_batch import ExamBatch
from app.models.branch import Branch
from app.models.exam_result import ExamResult
from app.models.placement_drive import PlacementDrive
from app.models.placement_application import PlacementApplication
from app.models.eligibility_rule import EligibilityRule
from app.models.academic_batch import AcademicBatch
from app.models.student_semester_summary import StudentSemesterSummary
from app.models.recruiter import Recruiter

from app.pipeline.parser import parse_file, ParserError
from app.pipeline.normalizer import normalize_dataframe
from app.pipeline.ingestion import ingest_records
from app.storage import upload_tsheet as _storage_upload, get_tsheet_signed_url
from app.models.audit_log import log_action


def _require_fields(data: dict, *fields):
    """Abort 400 if any of the given field names are missing or empty."""
    from flask import abort
    missing = [f for f in fields if not data.get(f)]
    if missing:
        abort(400, description=f"Missing required fields: {', '.join(missing)}")


def _get_or_create_admin_recruiter() -> str:
    """Return the ID of the sentinel Recruiter row used for admin-created drives."""
    sentinel = Recruiter.query.filter_by(company_name="__ADMIN__").first()
    if not sentinel:
        sentinel = Recruiter(company_name="__ADMIN__", contact_email="admin@vantage.os")
        db.session.add(sentinel)
        db.session.flush()
    return sentinel.id

# ──────────────────────────────────────────────────────────────────
#  T-SHEET PIPELINE — ASYNC RUNNER + SSE AUTH HELPER
# ──────────────────────────────────────────────────────────────────

def _verify_admin_token(token: str):
    """Verify a raw JWT string (for SSE ?token= query param). Returns User or None."""
    if not token:
        return None
    try:
        from flask_jwt_extended import decode_token
        decoded = decode_token(token)
        user_id = decoded.get("sub")
        user = db.session.get(User, user_id)
        if user and user.is_active and user.role >= RoleEnum.ADMIN:
            return user
        return None
    except Exception:
        return None


def _run_pipeline_async(app, upload_id: str, exam_batch_id: str, file_bytes: bytes, filename: str):
    """Background thread: parse → normalize → ingest. Updates current_stage on TSheetUpload."""
    with app.app_context():
        def _set_stage(stage: str):
            u = db.session.get(TSheetUpload, upload_id)
            if u:
                u.current_stage = stage
                db.session.commit()

        try:
            _set_stage("parsing")
            import io as _io
            df, _cols = parse_file(_io.BytesIO(file_bytes), filename)

            u = db.session.get(TSheetUpload, upload_id)
            u.total_rows_raw = len(df)
            db.session.commit()

            _set_stage("normalizing")
            records = normalize_dataframe(df)

            u = db.session.get(TSheetUpload, upload_id)
            u.total_records_parsed = len(records)
            db.session.commit()

            _set_stage("ingesting")
            committed, conflicts, new_students = ingest_records(upload_id, records, exam_batch_id)

            # ingest_records commits internally; re-fetch upload for final update
            u = db.session.get(TSheetUpload, upload_id)
            u.total_conflicts = conflicts
            u.new_students_created = new_students
            u.current_stage = "completed"
            u.upload_status = UploadStatusEnum.PARTIAL if conflicts > 0 else UploadStatusEnum.COMPLETED
            u.processing_completed_at = datetime.now(timezone.utc)
            db.session.commit()

            # Auto-notify admins of pipeline completion
            try:
                from app.models.notification import Notification
                if conflicts > 0:
                    Notification.notify_admins(
                        message=f"T-Sheet '{filename}' completed with {conflicts} conflict(s) requiring review.",
                        notif_type="tsheet_conflict",
                        title="T-Sheet Conflicts",
                        link="/admin/tsheets",
                    )
                else:
                    Notification.notify_admins(
                        message=f"T-Sheet '{filename}' ingested successfully: {committed} records committed.",
                        notif_type="tsheet_complete",
                        title="T-Sheet Complete",
                        link="/admin/tsheets",
                    )
                db.session.commit()
            except Exception:
                pass

        except ParserError as e:
            db.session.rollback()
            u = db.session.get(TSheetUpload, upload_id)
            if u:
                u.current_stage = "failed"
                u.upload_status = UploadStatusEnum.FAILED
                u.error_message = str(e)
                db.session.commit()

        except Exception as e:
            db.session.rollback()
            u = db.session.get(TSheetUpload, upload_id)
            if u:
                u.current_stage = "failed"
                u.upload_status = UploadStatusEnum.FAILED
                u.error_message = f"Pipeline error: {str(e)}"
                db.session.commit()


def _batch_to_dict(u: TSheetUpload) -> dict:
    return {
        "id": u.id,
        "filename": u.original_filename,
        "status": u.upload_status.value,
        "current_stage": u.current_stage or u.upload_status.value.lower(),
        "uploaded_at": u.uploaded_at.isoformat() if u.uploaded_at else None,
        "stats": {
            "parsed": u.total_records_parsed or 0,
            "conflicts": u.total_conflicts or 0,
            "new_students": u.new_students_created or 0,
        },
    }


# ──────────────────────────────────────────────────────────────────
#  T-SHEET PIPELINE ROUTES
# ──────────────────────────────────────────────────────────────────

@bp.route("/tsheets/upload", methods=["POST"])
@role_required(RoleEnum.ADMIN)
@limiter.limit("20 per hour")
def upload_tsheet():
    """
    POST /api/admin/tsheets/upload
    Form-data:
      - file: the T-Sheet file (CSV, XLSX)
      - branch_id: (UUID)
      - semester: (int)
      - exam_type: (REGULAR, SUPPLEMENTARY, IMPROVEMENT)
      - exam_month: (int)
      - exam_year or academic_year (int or "2024-25")

    Returns 202 immediately with upload_id. Pipeline runs in background.
    Subscribe to GET /api/admin/tsheets/batches/<id>/stream for live progress.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    branch_id = request.form.get("branch_id")
    semester = request.form.get("semester", type=int)
    exam_type = request.form.get("exam_type", "REGULAR")
    exam_month = request.form.get("exam_month", type=int)
    exam_year = request.form.get("exam_year", type=int)
    if not exam_year:
        raw_ay = request.form.get("academic_year", "")
        if raw_ay:
            exam_year = int(raw_ay.split("-")[-1]) if "-" in raw_ay else int(raw_ay)
    if not exam_month:
        exam_month = datetime.now(timezone.utc).month

    if not all([branch_id, semester, exam_year]):
        return jsonify({"error": "Missing required fields: branch_id, semester, exam_year (or academic_year)"}), 400

    file_bytes = file.read()

    # MIME type validation — reject files that aren't CSV or Excel
    try:
        import magic as _magic
        detected_mime = _magic.from_buffer(file_bytes, mime=True)
    except Exception:
        detected_mime = file.mimetype or ""
    _allowed_mimes = {
        "text/csv", "text/plain",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if detected_mime not in _allowed_mimes:
        return jsonify({
            "error": f"Unsupported file type '{detected_mime}'. Upload a CSV or Excel file.",
            "code": 415,
        }), 415

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Duplicate-file guard (before storage upload, avoids IntegrityError 500)
    existing_upload = TSheetUpload.query.filter_by(file_hash_sha256=file_hash).first()
    if existing_upload:
        return jsonify({
            "code": 409,
            "error": "This T-Sheet file has already been ingested.",
            "existing_upload_id": existing_upload.id,
        }), 409

    # Upload to Supabase Storage (falls back to local FS if key not configured)
    import uuid as _uuid
    batch_id = str(_uuid.uuid4())
    try:
        upload_meta = _storage_upload(file_bytes, file.filename, batch_id)
    except Exception:
        current_app.logger.exception("File storage upload failed")
        return jsonify({"code": 500, "error": "File storage failed. Try again."}), 500

    filename = upload_meta["original_filename"]
    storage_path = upload_meta["storage_path"]

    # Create ExamBatch if not exists
    ex_type_enum = ExamTypeEnum(exam_type)
    exam_batch = ExamBatch.query.filter_by(
        branch_id=branch_id,
        semester_number=semester,
        exam_type=ex_type_enum,
        exam_month=exam_month,
        exam_year=exam_year,
    ).first()
    if not exam_batch:
        exam_batch = ExamBatch(
            branch_id=branch_id,
            semester_number=semester,
            exam_type=ex_type_enum,
            exam_month=exam_month,
            exam_year=exam_year,
        )
        db.session.add(exam_batch)
        db.session.flush()

    # Create TSheetUpload record
    upload = TSheetUpload(
        uploaded_by=g.current_user.id,
        original_filename=filename,
        stored_path=storage_path,
        file_size_bytes=len(file_bytes),
        file_hash_sha256=file_hash,
        upload_status=UploadStatusEnum.PROCESSING,
        current_stage="uploading",
        processing_started_at=datetime.now(timezone.utc),
    )
    db.session.add(upload)
    db.session.flush()
    exam_batch.tsheet_upload_id = upload.id

    log_action(
        actor_id=g.current_user.id,
        actor_role=g.current_user.role.value,
        action="upload_tsheet",
        target_type="tsheet_upload",
        target_id=upload.id,
        new_value={
            "filename": filename,
            "branch_id": branch_id,
            "semester": semester,
            "exam_type": exam_type,
            "exam_year": exam_year,
        },
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"code": 409, "error": "Duplicate file — already ingested."}), 409

    # Kick off pipeline in background thread (pass bytes — no local file needed)
    app = current_app._get_current_object()
    t = threading.Thread(
        target=_run_pipeline_async,
        args=(app, upload.id, exam_batch.id, file_bytes, filename),
        daemon=True,
    )
    t.start()

    return jsonify({
        "message": "Upload accepted — pipeline running",
        "upload_id": upload.id,
        "status": "processing",
    }), 202


@bp.route("/tsheets/batches/<batch_id>/stream", methods=["GET"])
def stream_batch_progress(batch_id):
    """
    GET /api/admin/tsheets/batches/<batch_id>/stream?token=<jwt>
    SSE endpoint — emits stage events until pipeline completes or fails.
    Closes automatically on completed/partial/failed.
    """
    user = _verify_admin_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    def generate():
        terminal = {
            UploadStatusEnum.COMPLETED, UploadStatusEnum.PARTIAL, UploadStatusEnum.FAILED
        }
        # Map upload_status fallback → known stage name
        status_to_stage = {
            "processing": "ingesting",
            "pending":    "uploading",
            "completed":  "completed",
            "partial":    "completed",
            "failed":     "failed",
        }
        while True:
            try:
                # Always expire before reading so we never serve a stale object
                db.session.expire_all()
                u = db.session.get(TSheetUpload, batch_id)
                if not u:
                    yield f"data: {json.dumps({'stage': 'failed', 'error': 'Batch not found'})}\n\n"
                    break

                raw_stage = u.current_stage or status_to_stage.get(
                    u.upload_status.value.lower(), "ingesting"
                )
                payload = {
                    "stage": raw_stage,
                    "progress": {
                        "uploading": 10, "parsing": 30, "normalizing": 55,
                        "ingesting": 80, "completed": 100, "failed": 0,
                    }.get(raw_stage, 50),
                    "records_ingested": (u.total_records_parsed or 0) - (u.total_conflicts or 0),
                    "records_conflict": u.total_conflicts or 0,
                    "records_skipped": 0,
                    "error": u.error_message if u.upload_status == UploadStatusEnum.FAILED else None,
                }
                yield f"data: {json.dumps(payload)}\n\n"

                if u.upload_status in terminal:
                    break
            except Exception:
                current_app.logger.exception("stream_batch_progress SSE error")
                yield f"event: error\ndata: {json.dumps({'error': 'stream error'})}\n\n"
                break

            time.sleep(1)

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/tsheets/batches/<batch_id>/download", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def download_tsheet(batch_id):
    """GET /api/admin/tsheets/batches/<id>/download — Signed URL for the original file (5 min)."""
    upload = db.session.get(TSheetUpload, batch_id)
    if not upload:
        return jsonify({"code": 404, "error": "Upload not found"}), 404
    if not upload.stored_path:
        return jsonify({"code": 404, "error": "No file stored for this batch."}), 404
    try:
        signed_url = get_tsheet_signed_url(upload.stored_path, expires_in=300)
        return jsonify({"download_url": signed_url, "expires_in": 300})
    except RuntimeError as e:
        return jsonify({"code": 503, "error": str(e)}), 503


@bp.route("/tsheets/batches/stream", methods=["GET"])
def stream_batch_list():
    """
    GET /api/admin/tsheets/batches/stream?token=<jwt>
    SSE endpoint — emits batches_updated events every 3 seconds.
    Stays open permanently; client reconnects on disconnect.
    """
    user = _verify_admin_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    def generate():
        while True:
            try:
                db.session.expire_all()
                uploads = TSheetUpload.query.order_by(TSheetUpload.uploaded_at.desc()).limit(50).all()
                batches = [_batch_to_dict(u) for u in uploads]
                payload = json.dumps({"event": "batches_updated", "batches": batches})
                yield f"event: batches_updated\ndata: {payload}\n\n"
            except Exception:
                current_app.logger.exception("stream_batch_list SSE error")
                yield f"event: error\ndata: {json.dumps({'error': 'stream error'})}\n\n"
            time.sleep(3)

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/branches", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def list_branches():
    """GET /api/admin/branches — All branches for upload form selectors."""
    branches = Branch.query.filter(Branch.is_active != False).order_by(Branch.short_name).all()
    return jsonify([{"id": b.id, "short_name": b.short_name or b.branch_code, "name": b.name or b.short_name} for b in branches]), 200


@bp.route("/branches/seed", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def seed_branches():
    """POST /api/admin/branches/seed — Insert MGIT standard branches if table is empty."""
    existing = Branch.query.first()
    if existing:
        branches = Branch.query.filter(Branch.is_active != False).order_by(Branch.short_name).all()
        return jsonify([{"id": b.id, "short_name": b.short_name or b.branch_code, "name": b.name or b.short_name} for b in branches]), 200

    MGIT_BRANCHES = [
        {"branch_code": "01", "short_name": "CIVIL",  "name": "Civil Engineering"},
        {"branch_code": "02", "short_name": "EEE",    "name": "Electrical and Electronics Engineering"},
        {"branch_code": "03", "short_name": "MECH",   "name": "Mechanical Engineering"},
        {"branch_code": "04", "short_name": "ECE",    "name": "Electronics and Communication Engineering"},
        {"branch_code": "05", "short_name": "CSE",    "name": "Computer Science and Engineering"},
        {"branch_code": "12", "short_name": "IT",     "name": "Information Technology"},
        {"branch_code": "13", "short_name": "EIE",    "name": "Electronics and Instrumentation Engineering"},
        {"branch_code": "62", "short_name": "CSE-AI", "name": "CSE (Artificial Intelligence)"},
        {"branch_code": "66", "short_name": "CSE-DS", "name": "CSE (Data Science)"},
        {"branch_code": "67", "short_name": "CSE-CS", "name": "CSE (Cyber Security)"},
    ]
    created = []
    for b in MGIT_BRANCHES:
        row = Branch(branch_code=b["branch_code"], short_name=b["short_name"], name=b["name"], is_active=True)
        db.session.add(row)
        created.append(row)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify([{"id": b.id, "short_name": b.short_name, "name": b.name} for b in created]), 200


@bp.route("/tsheets/batches", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def list_batches():
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    pagination = TSheetUpload.query.order_by(TSheetUpload.uploaded_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        "items": [_batch_to_dict(u) for u in pagination.items],
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total,
        "pages": pagination.pages,
    }), 200


@bp.route("/tsheets/conflicts/<conflict_id>/resolve", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def resolve_conflict(conflict_id):
    """
    POST /api/admin/tsheets/conflicts/<id>/resolve
    JSON Body: { "action": "ACCEPT_TSHEET" | "KEEP_DB" }
    """
    record = db.session.get(TSheetRecord, conflict_id)
    if not record:
        return jsonify({"error": "Conflict record not found"}), 404
    if record.resolution_status != RecordStatusEnum.CONFLICT:
        return jsonify({"error": "Record is not in conflict state"}), 400
        
    data = request.get_json()
    action = data.get("action")
    
    if action not in ["ACCEPT_TSHEET", "KEEP_DB"]:
        return jsonify({"error": "Invalid action"}), 400
        
    record.admin_resolution = action
    record.resolved_by = g.current_user.id
    record.resolved_at = datetime.now(timezone.utc)
    
    if action == "ACCEPT_TSHEET":
        # Overwrite ExamResult with tsheet values
        res = db.session.get(ExamResult, record.resolved_result_id)
        if not res:
            return jsonify({"error": "Referenced exam result not found"}), 400

        gl_str = record.raw_grade_letter
        gl_enum = None
        if gl_str:
            gl_mapping = {
                "O": GradeLetterEnum.O, "A+": GradeLetterEnum.AP, "A": GradeLetterEnum.A,
                "B+": GradeLetterEnum.BP, "B": GradeLetterEnum.B, "C": GradeLetterEnum.C,
                "F": GradeLetterEnum.F, "AB": GradeLetterEnum.AB, "Ab": GradeLetterEnum.AB
            }
            gl_enum = gl_mapping.get(gl_str.upper())

        def _safe_int(val):
            try: return int(val)
            except: return None
        def _safe_float(val):
            try: return float(val)
            except: return None

        res.grade_letter = gl_enum or GradeLetterEnum.F
        res.grade_points = _safe_int(record.raw_grade_points) or 0
        res.total_marks = _safe_int(record.raw_total_marks)
        cr = _safe_float(record.raw_credits)
        res.credits_awarded = cr if (gl_enum not in [GradeLetterEnum.F, GradeLetterEnum.AB]) else 0.0
        
        record.resolution_status = RecordStatusEnum.COMMITTED
    else:
        record.resolution_status = RecordStatusEnum.SKIPPED
        
    db.session.commit()
    return jsonify({"message": f"Conflict resolved with {action}"}), 200


@bp.route("/tsheets/batches/<batch_id>/conflicts", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def list_batch_conflicts(batch_id):
    """GET /api/admin/tsheets/batches/<batch_id>/conflicts — List all conflicts for a batch."""
    upload = db.session.get(TSheetUpload, batch_id)
    if not upload:
        return jsonify({"error": "Batch not found"}), 404

    records = TSheetRecord.query.filter_by(
        tsheet_upload_id=batch_id,
        resolution_status=RecordStatusEnum.CONFLICT,
    ).all()

    data = []
    for r in records:
        data.append({
            "id": r.id,
            "raw_ht_no": r.raw_ht_no,
            "raw_subject_code": r.raw_course_code,
            "raw_subject_name": r.raw_course_name,
            "raw_grade_letter": r.raw_grade_letter,
            "raw_grade_points": r.raw_grade_points,
            "raw_total_marks": r.raw_total_marks,
            "raw_credits": r.raw_credits,
            "conflict_detail": r.conflict_detail,
            "resolution_status": r.resolution_status.value,
        })

    return jsonify({
        "batch_id": batch_id,
        "filename": upload.original_filename,
        "conflicts": data,
        "total": len(data),
    }), 200


@bp.route("/tsheets/batches/<batch_id>/resolve-all", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def resolve_all_conflicts(batch_id):
    """POST /api/admin/tsheets/batches/<batch_id>/resolve-all — Bulk resolve all conflicts."""
    upload = db.session.get(TSheetUpload, batch_id)
    if not upload:
        return jsonify({"error": "Batch not found"}), 404

    data = request.get_json(silent=True) or {}
    action = data.get("action", "accept_new")

    records = TSheetRecord.query.filter_by(
        tsheet_upload_id=batch_id,
        resolution_status=RecordStatusEnum.CONFLICT,
    ).all()

    resolved_count = 0
    for record in records:
        if action == "accept_new":
            record.admin_resolution = "ACCEPT_TSHEET"
            record.resolution_status = RecordStatusEnum.COMMITTED

            # Apply tsheet values to ExamResult
            if record.resolved_result_id:
                res = db.session.get(ExamResult, record.resolved_result_id)
                if res:
                    gl_str = record.raw_grade_letter
                    gl_mapping = {
                        "O": GradeLetterEnum.O, "A+": GradeLetterEnum.AP, "A": GradeLetterEnum.A,
                        "B+": GradeLetterEnum.BP, "B": GradeLetterEnum.B, "C": GradeLetterEnum.C,
                        "F": GradeLetterEnum.F, "AB": GradeLetterEnum.AB,
                    }
                    gl_enum = gl_mapping.get(gl_str.upper()) if gl_str else GradeLetterEnum.F
                    res.grade_letter = gl_enum or GradeLetterEnum.F
                    try:
                        res.grade_points = int(record.raw_grade_points) if record.raw_grade_points else 0
                    except (ValueError, TypeError):
                        res.grade_points = 0
        else:
            record.admin_resolution = "KEEP_DB"
            record.resolution_status = RecordStatusEnum.SKIPPED

        record.resolved_by = g.current_user.id
        record.resolved_at = datetime.now(timezone.utc)
        resolved_count += 1

    db.session.commit()
    return jsonify({"message": f"Resolved {resolved_count} conflicts", "resolved": resolved_count}), 200

# ══════════════════════════════════════════════════════════════════
#  MODULE 6 — ADMIN DASHBOARD
# ══════════════════════════════════════════════════════════════════

@bp.route("/dashboard", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def admin_dashboard():
    """GET /api/admin/dashboard — Aggregated KPIs and recent activity."""
    total_students = Student.query.filter(Student.deleted_at.is_(None)).count()
    verified_students = Student.query.filter(
        Student.profile_status == ProfileStatusEnum.VERIFIED,
        Student.deleted_at.is_(None),
    ).count()
    active_drives = PlacementDrive.query.filter(
        PlacementDrive.status == DriveStatusEnum.OPEN
    ).count()
    placements = PlacementApplication.query.filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED
    ).count()
    pending_batches = TSheetUpload.query.filter(
        TSheetUpload.upload_status.in_([UploadStatusEnum.PENDING, UploadStatusEnum.PROCESSING])
    ).count()

    verification_rate = round((verified_students / total_students * 100), 1) if total_students else 0.0

    return jsonify({
        "total_students": total_students,
        "verified_students": verified_students,
        "verification_rate": verification_rate,
        "active_drives": active_drives,
        "placements_this_year": placements,
        "pending_tsheet_batches": pending_batches,
    }), 200


# ══════════════════════════════════════════════════════════════════
#  MODULE 6 — STUDENT MANAGEMENT
# ══════════════════════════════════════════════════════════════════

@bp.route("/students/pending-verification-count", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def pending_verification_count():
    """GET /api/admin/students/pending-verification-count — Count unverified students."""
    count = Student.query.filter(
        Student.deleted_at.is_(None),
        Student.is_verified == False,  # noqa: E712
        Student.profile_status != ProfileStatusEnum.VERIFIED,
    ).count()
    return jsonify({"pending_count": count}), 200


@bp.route("/students/bulk-verify", methods=["PATCH"])
@role_required(RoleEnum.ADMIN)
def bulk_verify_students():
    """PATCH /api/admin/students/bulk-verify — Verify multiple students at once."""
    data = request.get_json(silent=True) or {}
    student_ids = data.get("student_ids", [])
    if not student_ids or not isinstance(student_ids, list):
        return jsonify({"error": "student_ids list is required"}), 400

    now = datetime.now(timezone.utc)
    updated = 0
    for sid in student_ids:
        s = db.session.get(Student, sid)
        if s and not s.is_verified:
            s.is_verified = True
            s.verification_source = "admin"
            s.verified_by = g.current_user.id
            s.verified_at = now
            s.profile_status = ProfileStatusEnum.VERIFIED
            # Sync User.is_verified
            _u = User.query.filter_by(student_id=s.id).first()
            if _u:
                _u.is_verified = True
            updated += 1

    db.session.commit()
    return jsonify({"message": f"{updated} students verified", "updated": updated}), 200


@bp.route("/students", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def list_students():
    """GET /api/admin/students — Paginated list with filters."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(per_page, 200)

    query = Student.query.options(joinedload(Student.academic_batch)).filter(Student.deleted_at.is_(None))

    # Filters
    branch_filter = request.args.get("branch")
    if branch_filter:
        branch = Branch.query.filter_by(short_name=branch_filter).first()
        if branch:
            batch_ids = [b.id for b in AcademicBatch.query.filter_by(branch_id=branch.id).all()]
            query = query.filter(Student.academic_batch_id.in_(batch_ids))

    verified = request.args.get("verified")
    if verified == "true":
        query = query.filter(Student.profile_status == ProfileStatusEnum.VERIFIED)
    elif verified == "false":
        query = query.filter(Student.profile_status != ProfileStatusEnum.VERIFIED)

    # is_verified filters by the boolean column (used by verification tabs)
    is_verified_param = request.args.get("is_verified")
    if is_verified_param == "true":
        query = query.filter(Student.is_verified == True)   # noqa: E712
    elif is_verified_param == "false":
        query = query.filter(Student.is_verified == False)  # noqa: E712

    min_cgpa = request.args.get("min_cgpa", type=float)
    if min_cgpa is not None:
        query = query.filter(Student.current_cgpa >= min_cgpa)

    max_cgpa = request.args.get("max_cgpa", type=float)
    if max_cgpa is not None:
        query = query.filter(Student.current_cgpa <= max_cgpa)

    grad_year = request.args.get("graduation_year", type=int)
    if grad_year:
        batch_ids = [b.id for b in AcademicBatch.query.filter_by(graduation_year=grad_year).all()]
        query = query.filter(Student.academic_batch_id.in_(batch_ids))

    search = request.args.get("search", "").strip()
    if search:
        query = query.filter(
            or_(
                Student.full_name.ilike(f"%{search}%"),
                Student.roll_number.ilike(f"%{search}%"),
            )
        )

    pagination = query.order_by(Student.roll_number.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Bulk-fetch branches for the current page in a single query (avoids N+1)
    branch_ids = {s.academic_batch.branch_id for s in pagination.items if s.academic_batch}
    branches_by_id = {b.id: b for b in Branch.query.filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}

    data = []
    for s in pagination.items:
        batch = s.academic_batch  # already eager-loaded
        branch_obj = branches_by_id.get(batch.branch_id) if batch else None
        is_verified = s.is_verified or s.profile_status == ProfileStatusEnum.VERIFIED
        data.append({
            "id": s.id,
            "roll_number": s.roll_number,
            "full_name": s.full_name,
            "branch": branch_obj.short_name if branch_obj else None,
            "cgpa": float(s.current_cgpa) if s.current_cgpa else 0.0,
            "backlogs": s.active_backlogs or 0,
            "profile_status": s.profile_status.value,
            "graduation_year": batch.graduation_year if batch else None,
            "is_verified": is_verified,
            "verification_source": s.verification_source,
            "verified_at": s.verified_at.isoformat() if s.verified_at else None,
        })

    return jsonify({
        "items": data,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total,
        "pages": pagination.pages,
    }), 200


@bp.route("/students/<student_id>", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def get_student(student_id):
    """GET /api/admin/students/<id> — Full student profile + academic records."""
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404

    batch = db.session.get(AcademicBatch, student.academic_batch_id) if student.academic_batch_id else None
    branch_obj = db.session.get(Branch, batch.branch_id) if batch else None

    summaries = StudentSemesterSummary.query.filter_by(student_id=student.id).order_by(
        StudentSemesterSummary.semester_number.asc()
    ).all()

    return jsonify({
        "id": student.id,
        "roll_number": student.roll_number,
        "full_name": student.full_name,
        "branch": branch_obj.short_name if branch_obj else None,
        "graduation_year": batch.graduation_year if batch else None,
        "cgpa": float(student.current_cgpa) if student.current_cgpa else 0.0,
        "backlogs": student.active_backlogs or 0,
        "total_backlogs_ever": student.total_backlogs_ever or 0,
        "semesters_completed": student.semesters_completed or 0,
        "profile_status": student.profile_status.value,
        "is_verified": student.is_verified or student.profile_status == ProfileStatusEnum.VERIFIED,
        "verification_source": student.verification_source,
        "verified_at": student.verified_at.isoformat() if student.verified_at else None,
        "phone": student.phone_primary,
        "college_email": student.college_email,
        "personal_email": student.personal_email,
        "admission_year": student.admission_year,
        "academic_records": [
            {
                "semester": s.semester_number,
                "sgpa": float(s.sgpa) if s.sgpa else None,
                "backlogs": s.backlogs_this_semester,
                "credits_earned": float(s.total_credits_earned) if s.total_credits_earned else None,
            }
            for s in summaries
        ],
    }), 200


@bp.route("/students/<student_id>", methods=["PATCH"])
@role_required(RoleEnum.ADMIN)
def update_student(student_id):
    """PATCH /api/admin/students/<id> — Admin override for any field."""
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404

    data = request.get_json(silent=True) or {}
    changes = []

    # Track changes for audit
    for field in ["full_name", "phone_primary", "personal_email", "college_email"]:
        if field in data:
            old_val = getattr(student, field)
            setattr(student, field, data[field])
            changes.append({"field": field, "old": old_val, "new": data[field]})

    # Academic overrides (audit-logged)
    if "current_cgpa" in data:
        old_val = float(student.current_cgpa) if student.current_cgpa else None
        student.current_cgpa = data["current_cgpa"]
        changes.append({"field": "current_cgpa", "old": old_val, "new": data["current_cgpa"]})

    if "active_backlogs" in data:
        old_val = student.active_backlogs
        student.active_backlogs = data["active_backlogs"]
        changes.append({"field": "active_backlogs", "old": old_val, "new": data["active_backlogs"]})

    if "profile_status" in data:
        old_val = student.profile_status.value
        student.profile_status = ProfileStatusEnum(data["profile_status"])
        if data["profile_status"] == "VERIFIED":
            student.verified_by = g.current_user.id
            student.verified_at = datetime.now(timezone.utc)
        changes.append({"field": "profile_status", "old": old_val, "new": data["profile_status"]})

    # is_verified / verification_source — admin manual override
    if "is_verified" in data:
        old_val = student.is_verified
        new_val = bool(data["is_verified"])
        student.is_verified = new_val
        source = data.get("verification_source", "admin")
        student.verification_source = source if new_val else None
        if new_val:
            student.verified_by = g.current_user.id
            student.verified_at = datetime.now(timezone.utc)
            # Promote profile_status to VERIFIED so eligibility engine picks it up
            student.profile_status = ProfileStatusEnum.VERIFIED
        else:
            student.verified_by = None
            student.verified_at = None
        # Sync User.is_verified so @verified_required decorator passes
        _linked_user = User.query.filter_by(student_id=student.id).first()
        if _linked_user:
            _linked_user.is_verified = new_val
        changes.append({"field": "is_verified", "old": old_val, "new": new_val, "source": source})

    if changes:
        log_action(
            actor_id=g.current_user.id,
            actor_role=g.current_user.role.value,
            action="update_student",
            target_type="student",
            target_id=student.id,
            old_value={c["field"]: c["old"] for c in changes},
            new_value={c["field"]: c["new"] for c in changes},
        )
    db.session.commit()
    return jsonify({"message": "Student updated", "changes": changes}), 200


@bp.route("/students/<student_id>/applications", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def student_applications(student_id):
    """GET /api/admin/students/<id>/applications — Application history."""
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404

    apps = PlacementApplication.query.filter_by(student_id=student_id).order_by(
        PlacementApplication.applied_at.desc()
    ).all()

    data = []
    for a in apps:
        drive = db.session.get(PlacementDrive, a.drive_id)
        data.append({
            "id": a.id,
            "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            "status": a.status.value,
            "offer_ctc": float(a.offer_ctc) if a.offer_ctc else None,
            "drive": {
                "id": drive.id,
                "drive_title": drive.drive_title,
                "role_title": drive.role_title,
            } if drive else None,
        })

    return jsonify(data), 200


# ══════════════════════════════════════════════════════════════════
#  MODULE 6 — DRIVE MANAGEMENT
# ══════════════════════════════════════════════════════════════════

@bp.route("/drives", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def create_drive():
    """POST /api/admin/drives — Create a new placement drive."""
    data = request.get_json(silent=True) or {}

    # Accept camelCase aliases sent by the frontend
    data.setdefault("drive_title", data.get("companyName"))
    data.setdefault("role_title", data.get("roleTitle"))
    data.setdefault("ctc_lpa", data.get("ctcLpa"))
    data.setdefault("application_close_at", data.get("applicationDeadline"))
    data.setdefault("allowed_branches", data.get("allowedBranches", []))

    _require_fields(data, "drive_title", "role_title", "ctc_lpa", "application_close_at")

    # Resolve recruiter_id: use provided value or fall back to admin sentinel
    recruiter_id = data.get("recruiter_id") or _get_or_create_admin_recruiter()

    try:
        job_type = DriveTypeEnum(data.get("job_type", "FULL_TIME"))
    except ValueError:
        return jsonify({"code": 400, "error": f"Invalid job_type: {data.get('job_type')}"}), 400

    drive = PlacementDrive(
        recruiter_id=recruiter_id,
        drive_title=data["drive_title"],
        role_title=data["role_title"],
        ctc_lpa=data.get("ctc_lpa"),
        job_location=data.get("job_location"),
        job_type=job_type,
        description=data.get("description"),
        status=DriveStatusEnum(data.get("status", "DRAFT")),
        application_open_at=datetime.fromisoformat(data["application_open_at"]) if data.get("application_open_at") else None,
        application_close_at=datetime.fromisoformat(data["application_close_at"]) if data.get("application_close_at") else None,
    )
    db.session.add(drive)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"code": 409, "error": "Drive could not be created — conflicting record."}), 409
    return jsonify({"message": "Drive created", "id": drive.id}), 201


@bp.route("/drives", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def list_drives():
    """GET /api/admin/drives — Paginated list of drives (all statuses)."""
    import json as _json
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    status_filter = request.args.get("status", "").upper()

    q = PlacementDrive.query
    if status_filter:
        try:
            q = q.filter(PlacementDrive.status == DriveStatusEnum(status_filter))
        except ValueError:
            pass
    pagination = q.order_by(PlacementDrive.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    drive_ids = [d.id for d in pagination.items]
    # Bulk-fetch application counts and eligibility rules in 2 queries
    from sqlalchemy import case
    counts = dict(
        db.session.query(
            PlacementApplication.drive_id,
            func.count(PlacementApplication.id),
        ).filter(PlacementApplication.drive_id.in_(drive_ids))
        .group_by(PlacementApplication.drive_id)
        .all()
    ) if drive_ids else {}
    rules_by_drive = {
        r.drive_id: r for r in EligibilityRule.query.filter(
            EligibilityRule.drive_id.in_(drive_ids)
        ).all()
    } if drive_ids else {}

    data = []
    for d in pagination.items:
        rule = rules_by_drive.get(d.id)
        allowed_branches = []
        if rule and rule.allowed_branches:
            try:
                allowed_branches = _json.loads(rule.allowed_branches)
            except Exception:
                allowed_branches = []
        data.append({
            "id": d.id,
            "drive_title": d.drive_title,
            "role_title": d.role_title,
            "ctc_lpa": float(d.ctc_lpa) if d.ctc_lpa else None,
            "job_type": d.job_type.value if d.job_type else None,
            "status": d.status.value,
            "applications_count": counts.get(d.id, 0),
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "application_deadline": d.application_close_at.isoformat() if d.application_close_at else None,
            "allowed_branches": allowed_branches,
            "min_cgpa": float(rule.min_cgpa) if rule and rule.min_cgpa else None,
        })
    return jsonify({
        "items": data,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total,
        "pages": pagination.pages,
    }), 200


@bp.route("/drives/<drive_id>", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def get_drive(drive_id):
    """GET /api/admin/drives/<id> — Single drive detail (all fields)."""
    import json as _json
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404

    rule = EligibilityRule.query.filter_by(drive_id=drive.id).first()
    extra = {}
    allowed_branches = []
    allowed_grad_years = []
    if rule:
        if rule.additional_criteria:
            try:
                extra = _json.loads(rule.additional_criteria)
            except Exception:
                extra = {}
        if rule.allowed_branches:
            try:
                allowed_branches = _json.loads(rule.allowed_branches)
            except Exception:
                allowed_branches = []

    # Recruiter email for "submitted by"
    recruiter_email = None
    from app.models.recruiter import Recruiter as RecruiterModel
    from app.models.user import User
    rec = db.session.get(RecruiterModel, drive.recruiter_id)
    if rec:
        user = User.query.filter_by(recruiter_id=rec.id).first()
        recruiter_email = user.email if user else None

    return jsonify({
        "id": drive.id,
        "drive_title": drive.drive_title,
        "role_title": drive.role_title,
        "ctc_lpa": float(drive.ctc_lpa) if drive.ctc_lpa else None,
        "location": drive.job_location,
        "job_type": drive.job_type.value,
        "status": drive.status.value,
        "job_description": drive.description,
        "application_deadline": drive.application_close_at.isoformat() if drive.application_close_at else None,
        "drive_date": drive.application_open_at.isoformat() if drive.application_open_at else None,
        "submitted_by": recruiter_email,
        "recruiter_email": recruiter_email,
        "openings": extra.get("openings"),
        "required_skills": extra.get("skills", []) if isinstance(extra.get("skills"), list) else ([extra["skills"]] if extra.get("skills") else []),
        "preferred_certifications": extra.get("preferred_certifications"),
        "bond_details": extra.get("bond"),
        "interview_rounds": extra.get("interview_rounds"),
        "notes": extra.get("notes"),
        "allowed_branches": allowed_branches,
        "allowed_graduation_years": extra.get("graduation_years", []),
        "min_cgpa": float(rule.min_cgpa) if rule and rule.min_cgpa else None,
        "max_active_backlogs": rule.max_active_backlogs if rule else None,
        "min_semesters_completed": rule.min_semesters_completed if rule else None,
    }), 200


@bp.route("/drives/<drive_id>", methods=["PATCH"])
@role_required(RoleEnum.ADMIN)
def update_drive(drive_id):
    """PATCH /api/admin/drives/<id> — Edit drive (blocked after completed)."""
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if drive.status == DriveStatusEnum.COMPLETED:
        return jsonify({"error": "Cannot edit a completed drive"}), 400

    data = request.get_json(silent=True) or {}
    for field in ["drive_title", "role_title", "job_location", "description"]:
        if field in data:
            setattr(drive, field, data[field])
    if "ctc_lpa" in data:
        drive.ctc_lpa = data["ctc_lpa"]
    old_status = drive.status
    if "status" in data:
        drive.status = DriveStatusEnum(data["status"])
    if "application_close_at" in data:
        drive.application_close_at = datetime.fromisoformat(data["application_close_at"]) if data["application_close_at"] else None

    db.session.commit()

    # Auto-notify on status transitions
    new_status = drive.status
    if old_status != new_status:
        try:
            from app.models.notification import Notification
            rec = db.session.get(Recruiter, drive.recruiter_id)
            recruiter_user = User.query.filter_by(recruiter_id=drive.recruiter_id).first() if drive.recruiter_id else None

            if new_status == DriveStatusEnum.OPEN:
                # Notify recruiter
                if recruiter_user:
                    Notification.create(
                        recipient_id=recruiter_user.id,
                        message=f"Your drive '{drive.role_title}' has been approved and is now open for applications.",
                        notif_type="drive_approved",
                        title="Drive Approved",
                        link=f"/recruiter/drives/{drive.id}",
                    )
                # Notify eligible students
                rules = EligibilityRule.query.filter_by(drive_id=drive.id, is_active=True).all()
                eligible_students = Student.query.filter(
                    Student.deleted_at.is_(None),
                    Student.profile_status == ProfileStatusEnum.VERIFIED,
                ).all()
                for s in eligible_students:
                    is_el = True
                    for rule in rules:
                        ok, _ = rule.evaluate(s)
                        if not ok:
                            is_el = False
                            break
                    if is_el:
                        su = User.query.filter_by(student_id=s.id).first()
                        if su:
                            Notification.create(
                                recipient_id=su.id,
                                message=f"New drive open: {drive.role_title} at {rec.company_name if rec else drive.drive_title}",
                                notif_type="drive_approved",
                                title="New Drive Available",
                                link=f"/student/drives/{drive.id}",
                            )

            elif new_status == DriveStatusEnum.CANCELLED:
                if recruiter_user:
                    rejection_note = data.get("rejection_reason", "")
                    Notification.create(
                        recipient_id=recruiter_user.id,
                        message=f"Your drive '{drive.role_title}' was rejected." + (f" Reason: {rejection_note}" if rejection_note else ""),
                        notif_type="drive_rejected",
                        title="Drive Rejected",
                        link=f"/recruiter/drives/{drive.id}",
                    )

            db.session.commit()
        except Exception:
            pass

    return jsonify({"message": "Drive updated"}), 200


@bp.route("/drives/<drive_id>", methods=["DELETE"])
@role_required(RoleEnum.ADMIN)
def delete_drive(drive_id):
    """DELETE /api/admin/drives/<id> — Soft-delete (set status=closed)."""
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    old_status = drive.status
    drive.status = DriveStatusEnum.CLOSED
    log_action(
        actor_id=g.current_user.id,
        actor_role=g.current_user.role.value,
        action="delete_drive",
        target_type="drive",
        target_id=drive_id,
        old_value={"status": old_status.value, "drive_title": drive.drive_title},
        new_value={"status": DriveStatusEnum.CLOSED.value},
    )
    db.session.commit()
    return jsonify({"message": "Drive closed"}), 200


@bp.route("/drives/<drive_id>/eligibility-rule", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def set_eligibility_rule(drive_id):
    """POST /api/admin/drives/<id>/eligibility-rule — Set/update rule."""
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404

    data = request.get_json(silent=True) or {}

    # Upsert: update existing or create new
    rule = EligibilityRule.query.filter_by(drive_id=drive.id).first()
    if not rule:
        rule = EligibilityRule(drive_id=drive.id)
        db.session.add(rule)

    rule.rule_name = data.get("rule_name", rule.rule_name)
    rule.min_cgpa = data.get("min_cgpa", rule.min_cgpa)
    rule.max_active_backlogs = data.get("max_active_backlogs", rule.max_active_backlogs)
    if "backlog_policy" in data:
        rule.backlog_policy = BacklogPolicyEnum(data["backlog_policy"])
    if "allowed_branches" in data:
        rule.allowed_branches = json.dumps(data["allowed_branches"]) if isinstance(data["allowed_branches"], list) else data["allowed_branches"]
    if "min_semesters_completed" in data:
        rule.min_semesters_completed = data["min_semesters_completed"]

    db.session.commit()
    return jsonify({"message": "Eligibility rule saved", "rule_id": rule.id}), 200


@bp.route("/drives/<drive_id>/eligible-students", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def eligible_students(drive_id):
    """GET /api/admin/drives/<id>/eligible-students — Preview eligible students."""
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404

    rules = EligibilityRule.query.filter_by(drive_id=drive.id, is_active=True).all()
    students = Student.query.filter(
        Student.deleted_at.is_(None),
        Student.profile_status == ProfileStatusEnum.VERIFIED,
    ).all()

    eligible = []
    for s in students:
        is_eligible = True
        for rule in rules:
            ok, _ = rule.evaluate(s)
            if not ok:
                is_eligible = False
                break
        if is_eligible:
            batch = db.session.get(AcademicBatch, s.academic_batch_id) if s.academic_batch_id else None
            branch_obj = db.session.get(Branch, batch.branch_id) if batch else None
            eligible.append({
                "id": s.id,
                "roll_number": s.roll_number,
                "full_name": s.full_name,
                "branch": branch_obj.short_name if branch_obj else None,
                "cgpa": float(s.current_cgpa) if s.current_cgpa else 0.0,
            })

    return jsonify({"count": len(eligible), "students": eligible}), 200


@bp.route("/drives/<drive_id>/applications", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def drive_applications(drive_id):
    """GET /api/admin/drives/<id>/applications — All applications for a drive."""
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404

    apps = PlacementApplication.query.filter_by(drive_id=drive_id).order_by(
        PlacementApplication.applied_at.desc()
    ).all()

    data = []
    for a in apps:
        student = db.session.get(Student, a.student_id)
        data.append({
            "id": a.id,
            "student": {
                "id": student.id if student else None,
                "roll_number": student.roll_number if student else None,
                "full_name": student.full_name if student else None,
            },
            "status": a.status.value,
            "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            "offer_ctc": float(a.offer_ctc) if a.offer_ctc else None,
        })

    return jsonify(data), 200


@bp.route("/applications/<app_id>/status", methods=["PATCH"])
@role_required(RoleEnum.ADMIN)
def update_application_status(app_id):
    """PATCH /api/admin/applications/<id>/status — Update application status (append-only)."""
    application = db.session.get(PlacementApplication, app_id)
    if not application:
        return jsonify({"error": "Application not found"}), 404

    data = request.get_json(silent=True) or {}
    new_status_str = data.get("status")
    if not new_status_str:
        return jsonify({"error": "Missing 'status' field"}), 400

    try:
        new_status = ApplicationStatusEnum(new_status_str.upper())
    except ValueError:
        return jsonify({"error": f"Invalid status: {new_status_str}"}), 400

    # Append-only: define allowed transitions
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

    # Allow REJECTED from any state, and otherwise enforce forward progression
    if new_status != ApplicationStatusEnum.REJECTED:
        current_idx = status_order.index(application.status) if application.status in status_order else -1
        new_idx = status_order.index(new_status) if new_status in status_order else -1
        if new_idx <= current_idx:
            return jsonify({"error": "Cannot revert to a previous status"}), 400

    application.status = new_status
    if new_status == ApplicationStatusEnum.SELECTED and "offer_ctc" in data:
        application.offer_ctc = data["offer_ctc"]

    db.session.commit()

    # Notify student of status change
    try:
        from app.models.notification import Notification
        student = db.session.get(Student, application.student_id)
        drive = db.session.get(PlacementDrive, application.drive_id)
        if student and drive:
            su = User.query.filter_by(student_id=student.id).first()
            if su:
                Notification.create(
                    recipient_id=su.id,
                    message=f"Your application for {drive.role_title} at {drive.drive_title} is now: {new_status.value}",
                    notif_type="application_update",
                    title="Application Status Updated",
                    link=f"/student/drives/{drive.id}",
                )
                db.session.commit()
    except Exception:
        pass

    return jsonify({"message": f"Application status updated to {new_status.value}"}), 200


# ══════════════════════════════════════════════════════════════════
#  MODULE 6 — ELIGIBILITY ENGINE
# ══════════════════════════════════════════════════════════════════

@bp.route("/eligibility/reevaluate", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def reevaluate_eligibility():
    """POST /api/admin/eligibility/reevaluate — Re-run eligibility for all open drives."""
    open_drives = PlacementDrive.query.filter(PlacementDrive.status == DriveStatusEnum.OPEN).all()
    verified_students = Student.query.filter(
        Student.deleted_at.is_(None),
        Student.profile_status == ProfileStatusEnum.VERIFIED,
    ).all()

    summary = {"drives_checked": 0, "students_evaluated": 0, "newly_eligible": 0, "newly_ineligible": 0}

    for drive in open_drives:
        rules = EligibilityRule.query.filter_by(drive_id=drive.id, is_active=True).all()
        summary["drives_checked"] += 1

        for student in verified_students:
            summary["students_evaluated"] += 1
            is_eligible = True
            for rule in rules:
                ok, _ = rule.evaluate(student)
                if not ok:
                    is_eligible = False
                    break

            was_eligible = student.is_placement_eligible
            if is_eligible and not was_eligible:
                summary["newly_eligible"] += 1
            elif not is_eligible and was_eligible:
                summary["newly_ineligible"] += 1

    return jsonify(summary), 200


@bp.route("/eligibility/drives/<drive_id>/check", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def check_student_eligibility(drive_id):
    """GET /api/admin/eligibility/drives/<id>/check?student_id=..."""
    student_id = request.args.get("student_id")
    if not student_id:
        return jsonify({"error": "student_id query parameter required"}), 400

    drive = db.session.get(PlacementDrive, drive_id)
    student = db.session.get(Student, student_id)

    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if not student:
        return jsonify({"error": "Student not found"}), 404

    rules = EligibilityRule.query.filter_by(drive_id=drive.id, is_active=True).all()
    all_reasons = []
    for rule in rules:
        ok, reasons = rule.evaluate(student)
        if not ok:
            all_reasons.extend(reasons)

    return jsonify({
        "student_id": student.id,
        "drive_id": drive.id,
        "is_eligible": len(all_reasons) == 0,
        "reasons": all_reasons,
    }), 200


# ══════════════════════════════════════════════════════════════════
#  MODULE 6 — ANALYTICS
# ══════════════════════════════════════════════════════════════════

@bp.route("/analytics/overview", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def analytics_overview():
    """GET /api/admin/analytics/overview — High-level placement stats."""
    total_students = Student.query.filter(Student.deleted_at.is_(None)).count()
    placed = PlacementApplication.query.filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED
    ).count()

    avg_ctc = db.session.query(func.avg(PlacementApplication.offer_ctc)).filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED,
        PlacementApplication.offer_ctc.isnot(None),
    ).scalar()

    highest_ctc = db.session.query(func.max(PlacementApplication.offer_ctc)).filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED,
    ).scalar()

    total_drives = PlacementDrive.query.count()
    # Unique companies
    total_companies = db.session.query(func.count(func.distinct(PlacementDrive.recruiter_id))).scalar() or 0

    # Company with highest CTC
    highest_ctc_row = db.session.query(
        PlacementDrive.drive_title,
        PlacementApplication.offer_ctc,
    ).join(
        PlacementApplication, PlacementApplication.drive_id == PlacementDrive.id
    ).filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED,
        PlacementApplication.offer_ctc.isnot(None),
    ).order_by(PlacementApplication.offer_ctc.desc()).first()

    return jsonify({
        "total_students": total_students,
        "placed_students": placed,
        "placement_percentage": round(placed / total_students * 100, 1) if total_students else 0.0,
        "avg_ctc_lpa": round(float(avg_ctc), 2) if avg_ctc else 0.0,
        "highest_ctc_lpa": float(highest_ctc) if highest_ctc else None,
        "highest_ctc_company": highest_ctc_row[0] if highest_ctc_row else None,
        "total_companies_visited": total_companies,
        "total_drives": total_drives,
    }), 200


@bp.route("/analytics/by-branch", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def analytics_by_branch():
    """GET /api/admin/analytics/by-branch — Placement rate by branch."""
    branches = Branch.query.all()
    result = []
    for branch in branches:
        batch_ids = [b.id for b in AcademicBatch.query.filter_by(branch_id=branch.id).all()]
        total = Student.query.filter(
            Student.academic_batch_id.in_(batch_ids),
            Student.deleted_at.is_(None),
        ).count()
        placed = 0
        if total > 0:
            student_ids = [s.id for s in Student.query.filter(
                Student.academic_batch_id.in_(batch_ids),
                Student.deleted_at.is_(None),
            ).all()]
            placed = PlacementApplication.query.filter(
                PlacementApplication.student_id.in_(student_ids),
                PlacementApplication.status == ApplicationStatusEnum.SELECTED,
            ).count()

        result.append({
            "branch": branch.short_name,
            "total_students": total,
            "placed_students": placed,
            "placement_rate": round(placed / total * 100, 1) if total else 0.0,
        })

    return jsonify(result), 200


@bp.route("/analytics/by-company", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def analytics_by_company():
    """GET /api/admin/analytics/by-company — Top companies by offers."""
    results = db.session.query(
        PlacementDrive.drive_title,
        func.count(PlacementApplication.id).label("offers"),
        func.avg(PlacementApplication.offer_ctc).label("avg_ctc"),
    ).join(
        PlacementApplication, PlacementApplication.drive_id == PlacementDrive.id
    ).filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED,
    ).group_by(PlacementDrive.id).order_by(func.count(PlacementApplication.id).desc()).limit(20).all()

    return jsonify([
        {
            "company": r[0],
            "offers": r[1],
            "avg_ctc_lpa": round(float(r[2]), 2) if r[2] else 0.0,
        }
        for r in results
    ]), 200


@bp.route("/analytics/cgpa-distribution", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def analytics_cgpa_distribution():
    """GET /api/admin/analytics/cgpa-distribution — CGPA histogram."""
    students = Student.query.filter(Student.deleted_at.is_(None)).all()
    buckets = {f"{i}-{i+1}": 0 for i in range(0, 10)}
    for s in students:
        cgpa = float(s.current_cgpa) if s.current_cgpa else 0.0
        bucket_key = f"{int(cgpa)}-{int(cgpa)+1}"
        if bucket_key in buckets:
            buckets[bucket_key] += 1

    return jsonify([{"range": k, "count": v} for k, v in buckets.items()]), 200


@bp.route("/analytics/export", methods=["GET"])
@role_required(RoleEnum.ADMIN)
@limiter.limit("10 per hour")
def analytics_export():
    """GET /api/admin/analytics/export — CSV export of all student placement data."""
    students = Student.query.filter(Student.deleted_at.is_(None)).order_by(Student.roll_number).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["roll_number", "name", "branch", "cgpa", "backlogs", "company", "role", "ctc", "status"])

    for s in students:
        batch = db.session.get(AcademicBatch, s.academic_batch_id) if s.academic_batch_id else None
        branch_obj = db.session.get(Branch, batch.branch_id) if batch else None

        # Get placement info (latest selected application)
        app = PlacementApplication.query.filter_by(
            student_id=s.id, status=ApplicationStatusEnum.SELECTED
        ).first()

        drive = db.session.get(PlacementDrive, app.drive_id) if app else None

        writer.writerow([
            s.roll_number,
            s.full_name or "",
            branch_obj.short_name if branch_obj else "",
            float(s.current_cgpa) if s.current_cgpa else 0.0,
            s.active_backlogs or 0,
            drive.drive_title if drive else "",
            drive.role_title if drive else "",
            float(app.offer_ctc) if app and app.offer_ctc else "",
            app.status.value if app else "NOT_PLACED",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=vantage_export.csv"},
    )


# ══════════════════════════════════════════════════════════════════
#  MODULE — ANALYTICS MONTHLY TREND
# ══════════════════════════════════════════════════════════════════

@bp.route("/analytics/placements", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def analytics_placements():
    """GET /api/admin/analytics/placements — Monthly placement trend."""
    import calendar
    from sqlalchemy import extract

    results = db.session.query(
        extract('year', PlacementApplication.applied_at).label('yr'),
        extract('month', PlacementApplication.applied_at).label('mo'),
        func.count(PlacementApplication.id).label('cnt'),
    ).filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED,
        PlacementApplication.applied_at.isnot(None),
    ).group_by('yr', 'mo').order_by('yr', 'mo').all()

    data = []
    cumulative = 0
    for r in results:
        cumulative += r.cnt
        data.append({
            "month": f"{calendar.month_abbr[int(r.mo)]} {int(r.yr)}",
            "count": r.cnt,
            "cumulative": cumulative,
        })

    return jsonify(data), 200


# ══════════════════════════════════════════════════════════════════
#  MODULE — NOTIFICATIONS (DB-backed)
# ══════════════════════════════════════════════════════════════════

@bp.route("/notifications", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def get_notifications():
    """GET /api/admin/notifications — Returns DB-backed notifications for this admin."""
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
@role_required(RoleEnum.ADMIN)
def mark_notifications_read():
    """PATCH /api/admin/notifications/mark-read — Mark notifications as read."""
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
@role_required(RoleEnum.ADMIN)
def delete_notification(notif_id):
    """DELETE /api/admin/notifications/<id> — Delete own notification."""
    from app.models.notification import Notification
    n = Notification.query.filter_by(id=notif_id, recipient_id=g.current_user.id).first()
    if not n:
        return jsonify({"error": "Notification not found"}), 404
    db.session.delete(n)
    db.session.commit()
    return jsonify({"ok": True}), 200


@bp.route("/message/recruiter", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def message_recruiter():
    """POST /api/admin/message/recruiter — Send a message to a recruiter."""
    from app.models.notification import Notification
    data = request.get_json(silent=True) or {}
    recruiter_user_id = data.get("recruiter_id")
    title = (data.get("title") or "").strip()
    message = (data.get("message") or "").strip()
    drive_id = data.get("drive_id")

    if not recruiter_user_id or not message:
        return jsonify({"error": "recruiter_id and message are required"}), 400

    target_user = db.session.get(User, recruiter_user_id)
    if not target_user or target_user.role != RoleEnum.RECRUITER:
        return jsonify({"error": "Recruiter not found"}), 404

    link = f"/recruiter/drives/{drive_id}" if drive_id else None
    Notification.create(
        recipient_id=recruiter_user_id,
        message=message,
        notif_type="recruiter_message",
        title=title or "Message from Admin",
        sender_id=g.current_user.id,
        link=link,
    )
    db.session.commit()
    return jsonify({"ok": True, "message": "Message sent to recruiter"}), 201


# ══════════════════════════════════════════════════════════════════
#  SSE — ADMIN EVENTS
# ══════════════════════════════════════════════════════════════════

@bp.route("/events", methods=["GET"])
def admin_events():
    """
    GET /api/admin/events?token=<jwt>
    SSE stream — emits real-time updates for drives, applications,
    verifications, and tsheet batch changes.
    """
    user = _verify_admin_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    admin_user_id = user.id

    def generate():
        last_drive_count = None
        last_app_count = None
        last_verified_count = None
        last_batch_stages = {}
        last_unread_notif_count = None

        while True:
            try:
                db.session.expire_all()
                events = []

                # Unread notifications
                from app.models.notification import Notification as _Notif
                cur_unread = _Notif.query.filter_by(
                    recipient_id=admin_user_id, is_read=False
                ).count()
                if last_unread_notif_count is not None and cur_unread > last_unread_notif_count:
                    events.append({
                        "type": "notification_created",
                        "data": {"unread_count": cur_unread},
                    })
                last_unread_notif_count = cur_unread

                # New drives
                cur_drives = PlacementDrive.query.count()
                if last_drive_count is not None and cur_drives > last_drive_count:
                    new_drives = PlacementDrive.query.order_by(
                        PlacementDrive.created_at.desc()
                    ).limit(cur_drives - last_drive_count).all()
                    for d in new_drives:
                        events.append({
                            "type": "drive_created",
                            "entity_id": d.id,
                            "data": {"drive_title": d.drive_title, "status": d.status.value},
                        })
                last_drive_count = cur_drives

                # New applications
                cur_apps = PlacementApplication.query.count()
                if last_app_count is not None and cur_apps > last_app_count:
                    events.append({
                        "type": "application_created",
                        "entity_id": None,
                        "data": {"new_count": cur_apps - last_app_count, "total": cur_apps},
                    })
                last_app_count = cur_apps

                # Verification changes
                cur_verified = Student.query.filter(
                    Student.is_verified == True,  # noqa: E712
                    Student.deleted_at.is_(None),
                ).count()
                if last_verified_count is not None and cur_verified != last_verified_count:
                    events.append({
                        "type": "verification_updated",
                        "entity_id": None,
                        "data": {"verified_count": cur_verified},
                    })
                last_verified_count = cur_verified

                # T-Sheet batch stage changes
                recent_batches = TSheetUpload.query.order_by(
                    TSheetUpload.uploaded_at.desc()
                ).limit(10).all()
                for b in recent_batches:
                    prev_stage = last_batch_stages.get(b.id)
                    cur_stage = b.current_stage or b.upload_status.value.lower()
                    if prev_stage is not None and prev_stage != cur_stage:
                        events.append({
                            "type": "tsheet_batch_updated",
                            "entity_id": b.id,
                            "data": {
                                "stage": cur_stage,
                                "status": b.upload_status.value,
                                "filename": b.original_filename,
                            },
                        })
                    last_batch_stages[b.id] = cur_stage

                for ev in events:
                    yield f"data: {json.dumps(ev)}\n\n"

                yield f": heartbeat\n\n"

            except Exception:
                current_app.logger.exception("admin_events SSE generator error")
                yield f"event: error\ndata: {json.dumps({'error': 'stream error'})}\n\n"

            time.sleep(5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════════════════════════════
#  RECRUITER MANAGEMENT
# ══════════════════════════════════════════════════════════════════

@bp.route("/recruiters", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def list_recruiters():
    """GET /api/admin/recruiters — All recruiter accounts with drive counts."""
    recruiter_users = User.query.filter_by(role=RoleEnum.RECRUITER).order_by(User.created_at.desc()).all()
    data = []
    for u in recruiter_users:
        rec = db.session.get(Recruiter, u.recruiter_id) if u.recruiter_id else None
        company_name = rec.company_name if rec else None
        recruiter_id = rec.id if rec else None
        total_drives = PlacementDrive.query.filter_by(recruiter_id=recruiter_id).count() if recruiter_id else 0
        active_drives = PlacementDrive.query.filter_by(
            recruiter_id=recruiter_id, status=DriveStatusEnum.OPEN
        ).count() if recruiter_id else 0
        data.append({
            "id": u.id,
            "recruiter_id": recruiter_id,
            "company_name": company_name or u.username,
            "recruiter_name": u.username,
            "email": u.email,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "total_drives": total_drives,
            "active_drives": active_drives,
            "is_active": u.is_active,
        })
    return jsonify(data), 200


@bp.route("/recruiters/<user_id>/status", methods=["PATCH"])
@role_required(RoleEnum.ADMIN)
def update_recruiter_status(user_id):
    """PATCH /api/admin/recruiters/<id>/status — Activate or deactivate recruiter account."""
    user = db.session.get(User, user_id)
    if not user or user.role != RoleEnum.RECRUITER:
        return jsonify({"error": "Recruiter not found"}), 404
    data = request.get_json(silent=True) or {}
    is_active = data.get("is_active")
    if is_active is None:
        user.is_active = not user.is_active
    else:
        user.is_active = bool(is_active)
    db.session.commit()
    return jsonify({"message": "Status updated", "is_active": user.is_active}), 200


# ══════════════════════════════════════════════════════════════════
#  DRIVE STATUS + RECRUITER NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════

@bp.route("/drives/<drive_id>/status", methods=["PATCH"])
@role_required(RoleEnum.ADMIN)
def update_drive_status(drive_id):
    """PATCH /api/admin/drives/<id>/status — Approve (open) or reject a drive."""
    from app.models.notification import Notification
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    data = request.get_json(silent=True) or {}
    new_status_str = data.get("status", "").upper()
    try:
        new_status = DriveStatusEnum(new_status_str)
    except ValueError:
        return jsonify({"error": f"Invalid status: {new_status_str}. Use OPEN, DRAFT, CLOSED, COMPLETED."}), 400

    old_status = drive.status
    drive.status = new_status
    db.session.flush()

    # Notify recruiter when drive is approved (OPEN) or rejected (CLOSED from DRAFT)
    recruiter_user = User.query.filter_by(
        recruiter_id=drive.recruiter_id, role=RoleEnum.RECRUITER
    ).first() if drive.recruiter_id else None
    if recruiter_user and old_status != new_status:
        if new_status == DriveStatusEnum.OPEN:
            Notification.create(
                recipient_id=recruiter_user.id,
                message=f"Your drive '{drive.role_title}' has been approved and is now open.",
                notif_type="drive",
                title="Drive Approved",
                sender_id=g.current_user.id,
                link="/recruiter/drives",
            )
        elif new_status == DriveStatusEnum.CLOSED and old_status == DriveStatusEnum.DRAFT:
            Notification.create(
                recipient_id=recruiter_user.id,
                message=f"Your drive '{drive.role_title}' was not approved.",
                notif_type="drive",
                title="Drive Not Approved",
                sender_id=g.current_user.id,
                link="/recruiter/drives",
            )

    log_action(
        actor_id=g.current_user.id,
        actor_role=g.current_user.role.value,
        action="update_drive_status",
        target_type="drive",
        target_id=drive_id,
        old_value={"status": old_status.value},
        new_value={"status": new_status.value},
    )
    db.session.commit()
    return jsonify({"message": f"Drive status updated to {drive.status.value}"}), 200


@bp.route("/drives/<drive_id>/notify-recruiter", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def notify_recruiter(drive_id):
    """POST /api/admin/drives/<id>/notify-recruiter — Send admin→recruiter notification."""
    from app.models.notification import Notification
    drive = db.session.get(PlacementDrive, drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    data = request.get_json(silent=True) or {}
    message_text = data.get("message", "").strip()
    if not message_text:
        return jsonify({"error": "message is required"}), 400
    subject = data.get("subject", "Message from Admin")

    # Find the recruiter's user account to deliver notification
    recruiter_user = User.query.filter_by(recruiter_id=drive.recruiter_id, role=RoleEnum.RECRUITER).first()
    if not recruiter_user:
        return jsonify({"error": "Recruiter user account not found"}), 404

    Notification.create(
        recipient_id=recruiter_user.id,
        message=message_text,
        notif_type="recruiter_message",
        title=subject,
        sender_id=g.current_user.id,
        link=f"/recruiter/drives",
    )
    db.session.commit()
    return jsonify({"message": "Notification sent to recruiter"}), 200


# ══════════════════════════════════════════════════════════════════
#  ANALYTICS — CGPA vs CTC CORRELATION
# ══════════════════════════════════════════════════════════════════

@bp.route("/analytics/cgpa-ctc-correlation", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def analytics_cgpa_ctc_correlation():
    """GET /api/admin/analytics/cgpa-ctc-correlation — Scatter data for placed students."""
    rows = db.session.query(
        Student.current_cgpa,
        PlacementApplication.offer_ctc,
        Student.academic_batch_id,
    ).join(
        PlacementApplication, PlacementApplication.student_id == Student.id
    ).filter(
        PlacementApplication.status == ApplicationStatusEnum.SELECTED,
        PlacementApplication.offer_ctc.isnot(None),
        Student.current_cgpa.isnot(None),
    ).order_by(Student.current_cgpa.asc()).all()

    if not rows:
        return jsonify({"data_points": [], "correlation_coefficient": None}), 200

    data_points = []
    for r in rows:
        batch = db.session.get(AcademicBatch, r.academic_batch_id) if r.academic_batch_id else None
        branch_obj = db.session.get(Branch, batch.branch_id) if batch else None
        data_points.append({
            "cgpa": float(r.current_cgpa),
            "ctc_lpa": float(r.offer_ctc),
            "branch": branch_obj.short_name if branch_obj else "Unknown",
        })

    # Pearson correlation coefficient
    n = len(data_points)
    corr = None
    if n >= 2:
        import statistics
        xs = [p["cgpa"] for p in data_points]
        ys = [p["ctc_lpa"] for p in data_points]
        try:
            corr = round(statistics.correlation(xs, ys), 4)
        except Exception:
            mean_x = sum(xs) / n
            mean_y = sum(ys) / n
            num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
            den_x = (sum((x - mean_x) ** 2 for x in xs)) ** 0.5
            den_y = (sum((y - mean_y) ** 2 for y in ys)) ** 0.5
            corr = round(num / (den_x * den_y), 4) if den_x * den_y else None

    return jsonify({"data_points": data_points, "correlation_coefficient": corr}), 200


# ══════════════════════════════════════════════════════════════════
#  CGPA RECALCULATION — ADMIN UTILITY
# ══════════════════════════════════════════════════════════════════

@bp.route("/students/recalculate-cgpa", methods=["POST"])
@role_required(RoleEnum.ADMIN)
def recalculate_cgpa():
    """POST /api/admin/students/recalculate-cgpa — Recompute CGPA for all students.

    Uses pure ORM queries — no raw SQL. Commits once per student so a failure
    on one record does not roll back the rest.
    """
    from app.listeners import recompute_cgpa_for_student
    students = Student.query.filter(Student.deleted_at.is_(None)).all()
    updated = 0
    errors = []
    for student in students:
        try:
            recompute_cgpa_for_student(student.id)
            db.session.commit()
            updated += 1
        except Exception as e:
            db.session.rollback()
            errors.append({"student_id": student.id, "error": str(e)})
    return jsonify({"updated": updated, "errors": errors}), 200


# ══════════════════════════════════════════════════════════════════
#  SEARCH
# ══════════════════════════════════════════════════════════════════

@bp.route("/search", methods=["GET"])
@role_required(RoleEnum.ADMIN)
def admin_search():
    """GET /api/admin/search?q=<term>&types=students,drives,recruiters"""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"students": [], "drives": [], "recruiters": []}), 200

    types_param = request.args.get("types", "students,drives,recruiters")
    types = {t.strip() for t in types_param.split(",")}
    result = {}
    like = f"%{q}%"

    if "students" in types:
        rows = Student.query.filter(
            Student.deleted_at.is_(None),
            or_(Student.full_name.ilike(like), Student.roll_number.ilike(like)),
        ).limit(5).all()
        result["students"] = [
            {
                "id": s.id,
                "roll_number": s.roll_number,
                "name": s.full_name,
                "cgpa": float(s.current_cgpa) if s.current_cgpa else 0.0,
            }
            for s in rows
        ]

    if "drives" in types:
        rows = PlacementDrive.query.filter(
            or_(
                PlacementDrive.drive_title.ilike(like),
                PlacementDrive.role_title.ilike(like),
            )
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

    if "recruiters" in types:
        recruiter_rows = Recruiter.query.filter(
            Recruiter.company_name.ilike(like)
        ).limit(5).all()
        result["recruiters"] = [
            {
                "id": r.id,
                "company_name": r.company_name,
                "recruiter_name": r.company_name,
            }
            for r in recruiter_rows
        ]

    return jsonify(result), 200
