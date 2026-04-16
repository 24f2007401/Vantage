"""
Vantage — Drive Message Models
DriveMessage + StudentMessageDelivery for admin→student communication.
"""
from datetime import datetime, timezone
from app.extensions import db
from app.models.enums import MessageTargetEnum


class DriveMessage(db.Model):
    """Admin message to students regarding a placement drive."""
    __tablename__ = "drive_message"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    drive_id = db.Column(db.String(36), db.ForeignKey("placement_drive.id"), nullable=False)
    sent_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    message_text = db.Column(db.Text, nullable=False)
    target_audience = db.Column(db.Enum(MessageTargetEnum), nullable=False)
    target_stages = db.Column(db.Text, nullable=True)  # JSON array of status values
    sent_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    delivery_method = db.Column(db.String(20), default="PORTAL")

    # ── Relationships ───────────────────────────────────────────
    deliveries = db.relationship(
        "StudentMessageDelivery", backref="drive_message", lazy="dynamic"
    )

    # ── Indexes ─────────────────────────────────────────────────
    __table_args__ = (
        db.Index("ix_drive_msg_drive", "drive_id"),
        db.Index("ix_drive_msg_sent_at", "sent_at"),
    )

    def __repr__(self):
        return f"<DriveMessage drive={self.drive_id} [{self.target_audience.value}]>"


class StudentMessageDelivery(db.Model):
    """Tracks delivery and read status per student per message."""
    __tablename__ = "student_message_delivery"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__('uuid').uuid4().hex)
    message_id = db.Column(db.String(36), db.ForeignKey("drive_message.id"), nullable=False)
    student_id = db.Column(db.String(36), db.ForeignKey("student.id"), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    read_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Constraints ─────────────────────────────────────────────
    __table_args__ = (
        db.UniqueConstraint("message_id", "student_id", name="uq_msg_delivery_student"),
        db.Index("ix_msg_delivery_student", "student_id"),
        db.Index("ix_msg_delivery_message", "message_id"),
    )

    def __repr__(self):
        return f"<MessageDelivery msg={self.message_id} student={self.student_id}>"
