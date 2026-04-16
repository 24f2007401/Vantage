"""
Vantage — AuditLog Model
Persistent, append-only log of sensitive admin operations.

Every row records WHO did WHAT to WHICH record, along with before/after values
and the request IP address. The table is append-only — rows are never updated
or deleted (compliance requirement).

Usage:
    from app.models.audit_log import log_action
    log_action(
        actor_id="...",
        actor_role="admin",
        action="update_student",
        target_type="student",
        target_id=student.id,
        old_value={"cgpa": 7.5},
        new_value={"cgpa": 8.1},
    )
    db.session.commit()   # commits the audit row together with the main change
"""
import uuid
from datetime import datetime, timezone
from flask import request as _flask_request
from app.extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id          = db.Column(
        db.String(32), primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )
    # Who performed the action
    actor_id    = db.Column(db.String(32), nullable=False)
    actor_role  = db.Column(db.String(20), nullable=False)

    # What was done
    action      = db.Column(db.String(50), nullable=False)

    # What was affected
    target_type = db.Column(db.String(50), nullable=True)
    target_id   = db.Column(db.String(32), nullable=True)

    # Before / after state (JSON columns)
    old_value   = db.Column(db.JSON, nullable=True)
    new_value   = db.Column(db.JSON, nullable=True)

    # Metadata
    ip_address  = db.Column(db.String(45), nullable=True)  # supports IPv6
    created_at  = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Most queries filter by actor or target — both indexed
        db.Index("ix_audit_actor",      "actor_id",    "created_at"),
        db.Index("ix_audit_target",     "target_type", "target_id"),
        db.Index("ix_audit_created_at", "created_at"),
    )

    def __repr__(self):
        return (
            f"<AuditLog {self.action} by {self.actor_id} "
            f"on {self.target_type}:{self.target_id}>"
        )


def log_action(
    actor_id: str,
    actor_role: str,
    action: str,
    target_type: str = None,
    target_id: str = None,
    old_value: dict = None,
    new_value: dict = None,
    ip: str = None,
) -> AuditLog:
    """
    Create an AuditLog entry and add it to the current session.

    IMPORTANT: does NOT commit. The caller must db.session.commit() so that
    the audit row and the business change are committed atomically.

    Args:
        actor_id:    User.id of the person performing the action.
        actor_role:  Their role string (e.g. "admin", "owner").
        action:      Short snake_case verb (e.g. "update_student", "verify_student").
        target_type: Model name of the affected record (e.g. "student", "drive").
        target_id:   Primary key of the affected record.
        old_value:   Dict snapshot of fields BEFORE the change.
        new_value:   Dict snapshot of fields AFTER the change.
        ip:          Request IP; auto-read from Flask request context if omitted.

    Returns:
        The (unsaved) AuditLog instance.
    """
    try:
        resolved_ip = ip or _flask_request.remote_addr
    except RuntimeError:
        # Outside request context (e.g. management commands)
        resolved_ip = None

    entry = AuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id else None,
        old_value=old_value,
        new_value=new_value,
        ip_address=resolved_ip,
    )
    db.session.add(entry)
    return entry
