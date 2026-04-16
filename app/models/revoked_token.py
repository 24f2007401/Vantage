"""
Vantage — RevokedToken Model
Database-backed JWT blocklist — used as a fallback when Redis is unavailable.

Every call to logout writes a row here (keyed by JTI) that expires at the
same time the original token expires. The token_in_blocklist_loader in
create_app() queries this table when Redis is not reachable.

Rows are safe to prune once expires_at < now() — add a nightly job or
call RevokedToken.cleanup() from an admin endpoint to keep the table small.
"""
from datetime import datetime, timezone
from app.extensions import db


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"

    # JTI is the JWT unique identifier — always a UUID string
    jti        = db.Column(db.String(36), primary_key=True)
    revoked_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Fast lookup by JTI + expiry — the only query pattern used
        db.Index("ix_revoked_token_jti_exp", "jti", "expires_at"),
        # Supports efficient cleanup of expired rows
        db.Index("ix_revoked_token_expires", "expires_at"),
    )

    @classmethod
    def revoke(cls, jti: str, exp_timestamp: float) -> "RevokedToken":
        """
        Add a JTI to the DB blocklist.

        Args:
            jti:           The JWT's unique identifier claim.
            exp_timestamp: The JWT's exp claim (Unix timestamp).

        Returns the new RevokedToken instance (not yet committed).
        Caller must db.session.commit() to persist.
        """
        from datetime import datetime, timezone
        expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        row = cls(jti=jti, expires_at=expires_at)
        db.session.add(row)
        return row

    @classmethod
    def cleanup(cls) -> int:
        """
        Delete all rows where the token has already expired.
        Returns the number of rows deleted.
        Safe to call from a scheduled job or admin endpoint.
        """
        now = datetime.now(timezone.utc)
        deleted = cls.query.filter(cls.expires_at <= now).delete(
            synchronize_session=False
        )
        db.session.commit()
        return deleted

    def __repr__(self):
        return f"<RevokedToken jti={self.jti} exp={self.expires_at}>"
