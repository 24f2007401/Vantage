"""
Vantage — Pipeline Stage 3: Ingestion
Responsibility: Compare normalized dict records against the DB. Write new records
to ExamResult, stage raw values in TSheetRecord, and handle conflicts safely.
"""
import json
import logging
import time
from typing import List, Dict, Any, Tuple
from sqlalchemy import text
from app.extensions import db
from app.models.student import Student
from app.models.course import Course
from app.models.branch import Branch
from app.models.exam_batch import ExamBatch
from app.models.exam_result import ExamResult
from app.models.tsheet_upload import TSheetUpload
from app.models.tsheet_record import TSheetRecord
from app.models.enums import RecordStatusEnum, GradeLetterEnum, ProfileStatusEnum

logger = logging.getLogger(__name__)


def ingest_records(
    upload_id: str,
    records: List[Dict[str, Any]],
    exam_batch_id: str
) -> Tuple[int, int, int]:
    """
    Ingests normalized records into the DB.

    Returns:
        tuple: (records_committed, records_conflicted, new_students_created)
    """
    upload = db.session.get(TSheetUpload, upload_id)
    exam_batch = db.session.get(ExamBatch, exam_batch_id)

    if not upload or not exam_batch:
        raise ValueError(f"Upload ID {upload_id} or ExamBatch ID {exam_batch_id} not found.")

    committed_count = 0
    conflict_count = 0
    new_students_count = 0
    ingested_student_ids = set()

    # Signal listeners to skip per-row recomputation during bulk insert
    db.session.info['bulk_ingesting'] = True

    total = len(records)
    start = time.time()

    for i, row in enumerate(records):
        # 1. Write the raw row to TSheetRecord staging table
        t_record = TSheetRecord(
            tsheet_upload_id=upload.id,
            raw_ht_no=row.get("raw_ht_no"),
            raw_course_code=row.get("raw_course_code"),
            raw_course_name=row.get("raw_course_name"),
            raw_cie_marks=row.get("raw_cie_marks"),
            raw_see_marks=row.get("raw_see_marks"),
            raw_total_marks=row.get("raw_total_marks"),
            raw_grade_letter=row.get("raw_grade_letter"),
            raw_grade_points=row.get("raw_grade_points"),
            raw_credits=row.get("raw_credits"),
            source_sheet_name=row.get("source_sheet_name"),
            source_row_number=row.get("source_row_number"),
            resolution_status=RecordStatusEnum.PENDING
        )
        db.session.add(t_record)
        db.session.flush()  # get t_record.id

        # Core identifiers
        ht_no = row.get("raw_ht_no")
        c_code = row.get("raw_course_code")

        # 2. Get or Create Student
        student = Student.query.filter_by(roll_number=ht_no).first()
        if not student:
            if len(ht_no) >= 8:
                branch_code_derived = ht_no[6:8]
            else:
                branch_code_derived = "XX"

            branch = Branch.query.filter_by(branch_code=branch_code_derived).first()
            if not branch:
                branch = Branch(
                    branch_code=branch_code_derived,
                    short_name=f"UNKNOWN-{branch_code_derived}",
                    needs_review=True
                )
                db.session.add(branch)
                db.session.flush()
                upload.new_branches_flagged = (upload.new_branches_flagged or 0) + 1

            from app.models.academic_batch import AcademicBatch
            from app.models.enums import BatchTypeEnum
            batch_stub = AcademicBatch.query.filter_by(branch_id=branch.id).first()
            if not batch_stub:
                batch_stub = AcademicBatch(
                    branch_id=branch.id,
                    admission_year=2000,
                    graduation_year=2004,
                    admission_type=BatchTypeEnum.REGULAR
                )
                db.session.add(batch_stub)
                db.session.flush()

            student = Student(
                roll_number=ht_no,
                academic_batch_id=batch_stub.id,
                admission_year=2000,
                profile_status=ProfileStatusEnum.PENDING
            )
            db.session.add(student)
            db.session.flush()

            from app.models.user import User
            from app.models.enums import RoleEnum
            student_email = f"{ht_no.lower()}@mgit.ac.in"
            if not User.query.filter_by(email=student_email).first():
                new_user = User(
                    email=student_email,
                    username=ht_no.lower(),
                    role=RoleEnum.STUDENT,
                    student_id=student.id,
                    is_active=True,
                    is_verified=False,
                    needs_password_change=True,
                )
                import bcrypt as _bcrypt
                new_user._password = _bcrypt.hashpw(
                    ht_no.encode("utf-8"), _bcrypt.gensalt(rounds=6)
                ).decode("utf-8")
                student.college_email = student_email
                db.session.add(new_user)
                db.session.flush()

            new_students_count += 1

        t_record.resolved_student_id = student.id
        ingested_student_ids.add(student.id)

        # 3. Get or Create Course (Stub)
        course = Course.query.filter_by(course_code=c_code).first()
        if not course:
            from app.models.enums import CourseTypeEnum
            _credits = float(row.get("raw_credits", 0) or 0)
            _is_mc = _credits == 0
            course = Course(
                course_code=c_code,
                course_name=row.get("raw_course_name", f"Unknown Course {c_code}"),
                course_type=CourseTypeEnum.MC if _is_mc else CourseTypeEnum.PC,
                semester_number=exam_batch.semester_number,
                credits=_credits,
                is_mandatory_non_credit=_is_mc,
            )
            db.session.add(course)
            db.session.flush()

        t_record.resolved_course_id = course.id

        # 4. Check for existing ExamResult
        existing_result = ExamResult.query.filter_by(
            student_id=student.id,
            exam_batch_id=exam_batch.id,
            course_id=course.id
        ).first()

        gl_str = row.get("raw_grade_letter")
        gl_enum = None
        if gl_str:
            gl_mapping = {
                "O": GradeLetterEnum.O, "A+": GradeLetterEnum.AP, "A": GradeLetterEnum.A,
                "B+": GradeLetterEnum.BP, "B": GradeLetterEnum.B, "C": GradeLetterEnum.C,
                "F": GradeLetterEnum.F, "AB": GradeLetterEnum.AB, "Ab": GradeLetterEnum.AB
            }
            gl_enum = gl_mapping.get(gl_str.strip().upper(), gl_mapping.get(gl_str.strip()))

        def _safe_int(val):
            try: return int(val)
            except (ValueError, TypeError): return None

        def _safe_float(val):
            try: return float(val)
            except (ValueError, TypeError): return None

        gp = _safe_int(row.get("raw_grade_points"))
        cr = _safe_float(row.get("raw_credits"))
        cie = _safe_int(row.get("raw_cie_marks"))
        see = _safe_int(row.get("raw_see_marks"))
        tot = _safe_int(row.get("raw_total_marks"))

        prev_attempts = ExamResult.query.filter_by(
            student_id=student.id,
            course_id=course.id
        ).count()
        attempt_no = existing_result.attempt_number if existing_result else (prev_attempts + 1)

        if not existing_result:
            from app.models.enums import CourseTypeEnum as _CTE
            is_mc = (course.course_type == _CTE.MC or course.is_mandatory_non_credit)
            new_res = ExamResult(
                student_id=student.id,
                exam_batch_id=exam_batch.id,
                course_id=course.id,
                cie_marks=cie,
                see_marks=see,
                total_marks=tot,
                grade_letter=gl_enum or GradeLetterEnum.F,
                grade_points=gp or 0,
                credits_awarded=cr if (gl_enum not in [GradeLetterEnum.F, GradeLetterEnum.AB]) else 0.0,
                is_absent=True if gl_enum == GradeLetterEnum.AB else False,
                is_mandatory_nc=is_mc,
                attempt_number=attempt_no,
                tsheet_record_id=t_record.id
            )
            db.session.add(new_res)
            db.session.flush()

            t_record.resolution_status = RecordStatusEnum.COMMITTED
            t_record.resolved_result_id = new_res.id
            committed_count += 1

        else:
            is_match = (
                existing_result.grade_letter == gl_enum and
                existing_result.grade_points == gp and
                existing_result.total_marks == tot
            )

            if is_match:
                t_record.resolution_status = RecordStatusEnum.DUPLICATE
                t_record.resolved_result_id = existing_result.id
            else:
                t_record.resolution_status = RecordStatusEnum.CONFLICT
                t_record.conflict_type = "GRADE_MISMATCH"
                t_record.conflict_detail = json.dumps({
                    "db_val": {
                        "grade": existing_result.grade_letter.value if existing_result.grade_letter else None,
                        "points": existing_result.grade_points,
                        "total": existing_result.total_marks
                    },
                    "tsheet_val": {
                        "grade": gl_enum.value if gl_enum else gl_str,
                        "points": gp,
                        "total": tot
                    }
                })
                t_record.resolved_result_id = existing_result.id
                conflict_count += 1

        # Batch commit every 100 records to avoid long-running transactions
        if (i + 1) % 100 == 0:
            db.session.commit()
            logger.info(
                f"[Pipeline] {i + 1}/{total} records committed "
                f"({time.time() - start:.1f}s elapsed)"
            )

    # Final commit for remaining records
    db.session.commit()
    logger.info(
        f"[Pipeline] Loop done — {total} records in {time.time() - start:.1f}s. "
        f"Recomputing {len(ingested_student_ids)} students."
    )

    # Clear bulk flag before per-student recomputation
    db.session.info.pop('bulk_ingesting', None)

    # Post-loop: recompute CGPA and profile status for every affected student.
    # Uses the pure ORM recalculation (no raw SQL) so it works against both
    # SQLite (tests) and PostgreSQL (production) without extra migration steps.
    from app.listeners import recompute_cgpa_for_student
    from app.models.user import User
    from datetime import datetime, timezone as _tz

    for idx, sid in enumerate(ingested_student_ids):
        try:
            recompute_cgpa_for_student(sid)

            # Sync profile_status from semesters_completed
            student_obj = db.session.get(Student, sid)
            if student_obj:
                from app.models.enums import ProfileStatusEnum as _PSE
                expected_sems = 6 if student_obj.lateral_entry else 8
                sems = student_obj.semesters_completed or 0
                if sems == 0:
                    student_obj.profile_status = _PSE.PENDING
                elif sems < expected_sems:
                    student_obj.profile_status = _PSE.PARTIAL
                else:
                    student_obj.profile_status = _PSE.VERIFIED

                # Auto-verify once profile is complete
                if student_obj.profile_status == _PSE.VERIFIED and not student_obj.is_verified:
                    now = datetime.now(_tz.utc)
                    student_obj.is_verified = True
                    student_obj.verification_source = "tsheet"
                    student_obj.verified_at = now
                    # Sync the linked User row so @verified_required passes
                    linked_user = User.query.filter_by(student_id=sid).first()
                    if linked_user and not linked_user.is_verified:
                        linked_user.is_verified = True

            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(f"[Pipeline] CGPA recompute failed for student {sid}")

        if (idx + 1) % 50 == 0:
            logger.info(f"[Pipeline] Recomputed {idx + 1}/{len(ingested_student_ids)} students")

    logger.info(
        f"[Pipeline] All done — committed={committed_count}, "
        f"conflicts={conflict_count}, new_students={new_students_count}"
    )

    return committed_count, conflict_count, new_students_count
