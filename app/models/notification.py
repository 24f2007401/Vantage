"""
Vantage — Notification Model
Persistent per-user notifications for all portal roles.
"""
import uuid as _uuid
from datetime import datetime, timezone
from app.extensions import db


class Notification(db.Model):
    """One notification row per user per event."""
    __tablename__ = "notification"

    id = db.Column(
        db.String(36), primary_key=True,
        default=lambda: _uuid.uuid4().hex,
    )
    recipient_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False,
    )
    sender_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=True,
    )  # NULL = system / automated event

    type = db.Column(db.String(50), nullable=False, default="system")
    # type values: system, tsheet_complete, tsheet_conflict,
    #   drive_submitted, drive_approved, drive_rejected, drive_closed,
    #   application_update, recruiter_message, admin_message, student_alert,
    #   eligibility_change

    title = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    link = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        db.Index("ix_notif_recipient_created", "recipient_id", "created_at"),
        db.Index("ix_notif_recipient_unread", "recipient_id", "is_read"),
    )

    # ── Helpers ─────────────────────────────────────────────────

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "is_read": self.is_read,
            "link": self.link,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    # ── Factory class methods ────────────────────────────────────

    @classmethod
    def create(cls, recipient_id, message, notif_type="system", title=None,
               sender_id=None, link=None):
        """Create and add to session. Caller must commit."""
        n = cls(
            recipient_id=recipient_id,
            sender_id=sender_id,
            type=notif_type,
            title=title,
            message=message,
            link=link,
        )
        db.session.add(n)
        return n

    @classmethod
    def notify_admins(cls, message, notif_type="system", title=None,
                      sender_id=None, link=None):
        """Create a notification for every active admin/owner user."""
        from app.models.user import User
        from app.models.enums import RoleEnum
        admins = User.query.filter(
            User.role >= RoleEnum.ADMIN,
            User.is_active.is_(True),
        ).all()
        for admin in admins:
            cls.create(admin.id, message, notif_type=notif_type, title=title,
                       sender_id=sender_id, link=link)

    def __repr__(self):
        return f"<Notification {self.type} → {self.recipient_id}>"
