from app.extensions import db
import datetime
import secrets


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"
    token      = db.Column(db.String(64), primary_key=True)
    user_id    = db.Column(db.String(32), db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used       = db.Column(db.Boolean, default=False)

    @staticmethod
    def generate(user_id: str) -> "PasswordResetToken":
        return PasswordResetToken(
            token=secrets.token_urlsafe(48),
            user_id=user_id,
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
        )
