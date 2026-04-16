"""
Vantage — Event Listeners
All SQLAlchemy event listeners implementing database-level consistency.
Registered at app startup via register_listeners().

IMPORTANT: after_insert/after_update listeners receive a `connection` object.
All DB writes inside these listeners MUST use connection.execute(text(...)) —
NOT db.session.add() or ORM attribute assignment — to avoid the SQLAlchemy
"flush process" warning and ensure updates actually persist.
"""
from datetime import datetime, timezone
from sqlalchemy import event, text
from app.extensions import db
from app.models.enums import (
    GradeLetterEnum, ProfileStatusEnum, UploadStatusEnum,
    BacklogPolicyEnum, ApplicationStatusEnum,
)


def register_listeners():
    """Wire up all event listeners. Call this from create_app()."""
    from app.models.exam_result import ExamResult
    from app.models.tsheet_upload import TSheetUpload
    from app.models.placement_application import PlacementApplication

    # ── Listener 1 — SGPA/CGPA Recomputation ────────────────────
    @event.listens_for(ExamResult, "after_insert")
    @event.listens_for(ExamResult, "after_update")
    def recompute_academic_summary(mapper, connection, target):
        """Recompute SGPA, CGPA, backlogs on every ExamResult change."""
        if db.session.info.get('bulk_ingesting'):
            return  # Deferred to post-loop recomputation
        _recompute_student_academic_summary(target.student_id, connection)

    # ── Listener 2 — Profile Status Update ──────────────────────
    @event.listens_for(ExamResult, "after_insert")
    def update_profile_status(mapper, connection, target):
        """Update student.profile_status based on semesters present."""
        if db.session.info.get('bulk_ingesting'):
            return  # Deferred to post-loop recomputation
        _update_student_profile_status(target.student_id, connection)

    # ── Listener 3 — Placement Eligibility Recomputation ────────
    @event.listens_for(ExamResult, "after_insert")
    @event.listens_for(ExamResult, "after_update")
    def recompute_placement_eligibility(mapper, connection, target):
        """Recompute is_placement_eligible after CGPA/backlogs change."""
        if db.session.info.get('bulk_ingesting'):
            return  # Deferred to post-loop recomputation
        _recompute_placement_eligibility(target.student_id, connection)

    # ── Listener 4 — Backlog Clearance on Supplementary ─────────
    @event.listens_for(ExamResult, "after_insert")
    def handle_supplementary_clearance(mapper, connection, target):
        """Mark previous F/Ab as superseded when supplementary passes."""
        if target.attempt_number > 1:
            _handle_supplementary_result(target, connection)

    # ── Listener 5 — Duplicate File Guard ───────────────────────
    @event.listens_for(TSheetUpload, "before_insert")
    def check_duplicate_file(mapper, connection, target):
        """Prevent re-ingesting the same file by SHA-256 hash."""
        if target.file_hash_sha256:
            row = connection.execute(
                text(
                    "SELECT id FROM tsheet_upload "
                    "WHERE file_hash_sha256 = :h "
                    "AND upload_status IN ('COMPLETED', 'PARTIAL') "
                    "LIMIT 1"
                ),
                {"h": target.file_hash_sha256},
            ).fetchone()
            if row:
                raise ValueError(
                    f"This T-Sheet file has already been ingested. "
                    f"Existing upload ID: {row[0]}. Hash: {target.file_hash_sha256}"
                )

    # ── Listener 6 — Final Placement Denormalization ────────────
    @event.listens_for(PlacementApplication, "after_update")
    def denormalize_final_placement(mapper, connection, target):
        """Update student.final_placed_company when status = SELECTED."""
        if target.status == ApplicationStatusEnum.SELECTED:
            _denormalize_placement(target, connection)


# ═══════════════════════════════════════════════════════════════
# Implementation helpers — all use connection.execute(text(...))
# ═══════════════════════════════════════════════════════════════

def _recompute_student_academic_summary(student_id, connection):
    """
    Listener 1 — Recompute SGPA per semester, CGPA, and backlog counts.
    Uses a single JOIN query instead of N+N round-trips.
    """
    student_row = connection.execute(
        text("SELECT lateral_entry FROM student WHERE id = :sid"),
        {"sid": student_id},
    ).fetchone()
    if not student_row:
        return

    passing = {GradeLetterEnum.F.value, GradeLetterEnum.AB.value}

    # Single JOIN — fetches all active results with semester + course credits
    results = connection.execute(
        text("""
            SELECT er.id, er.grade_letter, er.grade_points, er.credits_awarded,
                   er.is_mandatory_nc, er.course_id,
                   eb.semester_number, eb.id AS exam_batch_id,
                   c.credits AS course_credits
            FROM exam_result er
            JOIN exam_batch eb ON eb.id = er.exam_batch_id
            JOIN course c ON c.id = er.course_id
            WHERE er.student_id = :sid AND er.superseded_by IS NULL
        """),
        {"sid": student_id},
    ).fetchall()

    # Group by semester
    sem_data = {}
    for r in results:
        sem = r.semester_number
        if sem not in sem_data:
            sem_data[sem] = {"results": [], "exam_batch_id": r.exam_batch_id}
        sem_data[sem]["results"].append(r)

    now_str = datetime.now(timezone.utc).isoformat()

    # Reset is_latest=False for ALL existing semester summaries for this student
    # before upserting so that only the latest semester ends up with is_latest=True
    connection.execute(
        text("UPDATE student_semester_summary SET is_latest = :f WHERE student_id = :sid"),
        {"f": False, "sid": student_id},
    )

    latest_sem_num = max(sem_data.keys()) if sem_data else None
    sem_backlogs = {}  # sem_num → backlogs_this_semester

    # Per-semester SGPA upsert
    for sem_num, data in sem_data.items():
        credit_bearing = [r for r in data["results"] if not r.is_mandatory_nc]

        sgpa_num = sum(
            float(r.grade_points) * float(r.course_credits)
            for r in credit_bearing
            if r.grade_letter not in passing
        )
        sgpa_den = sum(float(r.course_credits) for r in credit_bearing)
        sgpa = round(sgpa_num / sgpa_den, 2) if sgpa_den > 0 else 0.0

        backlogs_sem = sum(1 for r in data["results"] if r.grade_letter in passing)
        total_earned = sum(
            float(r.credits_awarded) for r in credit_bearing
            if r.grade_letter not in passing
        )

        sem_backlogs[sem_num] = backlogs_sem
        is_latest_for_sem = (sem_num == latest_sem_num)

        existing = connection.execute(
            text("""
                SELECT id FROM student_semester_summary
                WHERE student_id = :sid AND semester_number = :sem
                  AND exam_batch_id = :ebid
                LIMIT 1
            """),
            {"sid": student_id, "sem": sem_num, "ebid": data["exam_batch_id"]},
        ).fetchone()

        if existing:
            connection.execute(
                text("""
                    UPDATE student_semester_summary
                    SET sgpa = :sgpa,
                        total_credits_registered = :reg,
                        total_credits_earned = :earned,
                        backlogs_this_semester = :bl,
                        computed_at = :now,
                        is_latest = :il
                    WHERE id = :id
                """),
                {"sgpa": sgpa, "reg": sgpa_den, "earned": total_earned,
                 "bl": backlogs_sem, "now": now_str, "il": is_latest_for_sem, "id": existing[0]},
            )
        else:
            import uuid
            connection.execute(
                text("""
                    INSERT INTO student_semester_summary
                        (id, student_id, semester_number, exam_batch_id,
                         sgpa, total_credits_registered, total_credits_earned,
                         backlogs_this_semester, is_latest, computed_at)
                    VALUES (:id, :sid, :sem, :ebid, :sgpa, :reg, :earned, :bl, :il, :now)
                """),
                {
                    "id": uuid.uuid4().hex,
                    "sid": student_id, "sem": sem_num, "ebid": data["exam_batch_id"],
                    "sgpa": sgpa, "reg": sgpa_den, "earned": total_earned,
                    "bl": backlogs_sem, "il": is_latest_for_sem, "now": now_str,
                },
            )

    # CGPA — credit-weighted mean across ALL semesters (NOT arithmetic mean of SGPAs)
    # Formula: SUM(grade_points_i × credits_i for all subjects) / SUM(credits_i)
    all_gp_weighted = sum(
        float(r.grade_points) * float(r.course_credits)
        for v in sem_data.values()
        for r in v["results"]
        if not r.is_mandatory_nc
    )
    all_credits_total = sum(
        float(r.course_credits)
        for v in sem_data.values()
        for r in v["results"]
        if not r.is_mandatory_nc
    )
    cgpa = round(all_gp_weighted / all_credits_total, 2) if all_credits_total > 0 else 0.0

    # active_backlogs = sum of uncleared (non-superseded) F/Ab across ALL semesters
    active_backlogs = sum(sem_backlogs.values()) if sem_backlogs else 0

    # Total backlogs ever (including superseded)
    all_grades = connection.execute(
        text("SELECT grade_letter FROM exam_result WHERE student_id = :sid"),
        {"sid": student_id},
    ).fetchall()
    total_backlogs_ever = sum(1 for r in all_grades if r.grade_letter in passing)

    connection.execute(
        text("""
            UPDATE student
            SET current_cgpa = :cgpa,
                active_backlogs = :ab,
                total_backlogs_ever = :tbe,
                semesters_completed = :sc,
                last_tsheet_synced = :now
            WHERE id = :sid
        """),
        {
            "cgpa": cgpa, "ab": active_backlogs, "tbe": total_backlogs_ever,
            "sc": len(sem_data), "now": now_str, "sid": student_id,
        },
    )


def _update_student_profile_status(student_id, connection):
    """
    Listener 2 — Set profile_status based on semesters covered.
    """
    student_row = connection.execute(
        text("SELECT lateral_entry FROM student WHERE id = :sid"),
        {"sid": student_id},
    ).fetchone()
    if not student_row:
        return

    expected = 6 if student_row.lateral_entry else 8

    sem_count = connection.execute(
        text("""
            SELECT COUNT(DISTINCT eb.semester_number)
            FROM exam_result er
            JOIN exam_batch eb ON eb.id = er.exam_batch_id
            WHERE er.student_id = :sid
        """),
        {"sid": student_id},
    ).scalar() or 0

    if sem_count == 0:
        status = ProfileStatusEnum.PENDING.value
    elif sem_count < expected:
        status = ProfileStatusEnum.PARTIAL.value
    else:
        status = ProfileStatusEnum.VERIFIED.value

    connection.execute(
        text("UPDATE student SET profile_status = :s WHERE id = :sid"),
        {"s": status, "sid": student_id},
    )

    # When tsheet data covers all semesters → auto-verify (only if not already verified)
    if status == ProfileStatusEnum.VERIFIED.value:
        now_str = datetime.now(timezone.utc).isoformat()
        connection.execute(
            text("""
                UPDATE student
                SET is_verified = TRUE,
                    verification_source = 'tsheet',
                    verified_at = :now
                WHERE id = :sid AND (is_verified = FALSE OR is_verified IS NULL)
            """),
            {"now": now_str, "sid": student_id},
        )
        # Sync User.is_verified so @verified_required decorator passes
        connection.execute(
            text("""
                UPDATE users
                SET is_verified = TRUE
                WHERE student_id = :sid AND (is_verified = FALSE OR is_verified IS NULL)
            """),
            {"sid": student_id},
        )


def _handle_supplementary_result(new_result, connection):
    """
    Listener 4 — Mark previous F/Ab as superseded when supplementary passes.
    """
    passing = {GradeLetterEnum.F.value, GradeLetterEnum.AB.value}
    # new_result.grade_letter is an ORM enum instance — normalise to its .value
    # so the comparison matches what PostgreSQL/SQLite stores (enum name, not value)
    new_gl_name = getattr(new_result.grade_letter, 'value', str(new_result.grade_letter))
    if new_gl_name in passing:
        return  # new result is also a fail — don't supersede

    prev = connection.execute(
        text("""
            SELECT id, grade_letter FROM exam_result
            WHERE student_id = :sid AND course_id = :cid
              AND attempt_number < :att
            ORDER BY attempt_number DESC
            LIMIT 1
        """),
        {
            "sid": new_result.student_id,
            "cid": new_result.course_id,
            "att": new_result.attempt_number,
        },
    ).fetchone()

    if prev and prev.grade_letter in passing:
        connection.execute(
            text("UPDATE exam_result SET superseded_by = :new_id WHERE id = :old_id"),
            {"new_id": new_result.id, "old_id": prev.id},
        )
        # Raw SQL update doesn't fire ORM after_update, so recompute manually
        _recompute_student_academic_summary(new_result.student_id, connection)


def _recompute_placement_eligibility(student_id, connection):
    """
    Listener 3 — Recompute student.is_placement_eligible using institution-level rules.
    """
    student_row = connection.execute(
        text("""
            SELECT current_cgpa, active_backlogs, total_backlogs_ever,
                   semesters_completed, profile_status, lateral_entry
            FROM student WHERE id = :sid
        """),
        {"sid": student_id},
    ).fetchone()
    if not student_row:
        return

    rules = connection.execute(
        text("""
            SELECT min_cgpa, max_active_backlogs,
                   min_semesters_completed, backlog_policy
            FROM eligibility_rule
            WHERE is_active = true AND drive_id IS NULL
        """),
    ).fetchall()

    if not rules:
        connection.execute(
            text("UPDATE student SET is_placement_eligible = :e WHERE id = :sid"),
            {"e": False, "sid": student_id},
        )
        return

    cgpa = float(student_row.current_cgpa or 0)
    active_bl = student_row.active_backlogs or 0
    total_bl = student_row.total_backlogs_ever or 0
    sems = student_row.semesters_completed or 0

    eligible = False
    for rule in rules:
        passes = True
        if rule.min_cgpa is not None and cgpa < float(rule.min_cgpa):
            passes = False
        if rule.max_active_backlogs is not None and active_bl > rule.max_active_backlogs:
            passes = False
        if rule.backlog_policy == BacklogPolicyEnum.NEVER_HAD.value and total_bl > 0:
            passes = False
        if rule.min_semesters_completed is not None and sems < rule.min_semesters_completed:
            passes = False
        if passes:
            eligible = True
            break

    connection.execute(
        text("UPDATE student SET is_placement_eligible = :e WHERE id = :sid"),
        {"e": eligible, "sid": student_id},
    )


def recompute_cgpa_for_student(student_id: str) -> None:
    """
    Pure ORM CGPA recalculation — safe to call after any commit.

    Updates student.current_cgpa, active_backlogs, total_backlogs_ever,
    semesters_completed, and stages the student row for the caller to commit.

    Formula:
        CGPA = SUM(grade_points × course.credits  for non-MC, non-superseded rows)
               / SUM(course.credits               for non-MC, non-superseded rows)

    Backlogs:
        active_backlogs    = F/AB rows WHERE superseded_by IS NULL  (across all sems)
        total_backlogs_ever = F/AB rows (all, incl. superseded)
    """
    from app.models.student import Student
    from app.models.exam_result import ExamResult

    student = db.session.get(Student, student_id)
    if not student:
        return

    _failing = {GradeLetterEnum.F, GradeLetterEnum.AB}

    # ── Active results (non-superseded) ─────────────────────────────
    active_results = ExamResult.query.filter(
        ExamResult.student_id == student_id,
        ExamResult.superseded_by.is_(None),
    ).all()

    # ── All results (for total_backlogs_ever) ────────────────────────
    all_results = ExamResult.query.filter_by(student_id=student_id).all()

    gp_weighted = 0.0
    credits_total = 0.0
    active_backlogs = 0
    sem_numbers: set = set()

    for r in active_results:
        eb = r.exam_batch  # lazy-loaded via ExamBatch.exam_results backref
        if eb:
            sem_numbers.add(eb.semester_number)

        # Backlogs counted for ALL subjects (including MC)
        if r.grade_letter in _failing:
            active_backlogs += 1

        if r.is_mandatory_nc:
            continue  # MC excluded from CGPA numerator and denominator

        course = r.course  # lazy-loaded via Course.exam_results backref
        course_credits = float(course.credits) if course and course.credits else 0.0
        gp_weighted += float(r.grade_points or 0) * course_credits
        credits_total += course_credits

    cgpa = round(gp_weighted / credits_total, 2) if credits_total > 0 else 0.0

    total_backlogs_ever = sum(
        1 for r in all_results if r.grade_letter in _failing
    )

    student.current_cgpa = cgpa
    student.active_backlogs = active_backlogs
    student.total_backlogs_ever = total_backlogs_ever
    student.semesters_completed = len(sem_numbers)
    student.last_tsheet_synced = datetime.now(timezone.utc)

    db.session.add(student)


def _denormalize_placement(application, connection):
    """
    Listener 6 — Copy final placement info to student table.
    """
    drive_row = connection.execute(
        text("SELECT drive_title FROM placement_drive WHERE id = :did"),
        {"did": application.drive_id},
    ).fetchone()
    if not drive_row:
        return

    connection.execute(
        text("""
            UPDATE student
            SET final_placed_company = :company,
                final_ctc_lpa = :ctc
            WHERE id = :sid
        """),
        {
            "company": drive_row.drive_title,
            "ctc": float(application.offer_ctc) if application.offer_ctc else None,
            "sid": application.student_id,
        },
    )
