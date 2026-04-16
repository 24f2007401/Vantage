"""
Vantage — Global Error Handlers
Registers consistent JSON error responses and per-request ID headers.
"""
from uuid import uuid4
from flask import jsonify, g
from sqlalchemy.exc import IntegrityError, OperationalError


def register_error_handlers(app):
    # ── Request ID ──────────────────────────────────────────────
    @app.before_request
    def _set_request_id():
        g.request_id = str(uuid4())

    @app.after_request
    def _add_request_id_header(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "-")
        return response

    # ── HTTP error handlers ─────────────────────────────────────
    @app.errorhandler(400)
    def bad_request(e):
        details = getattr(e, "description", None)
        return jsonify({"code": 400, "error": "Bad request", "details": details}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"code": 401, "error": "Authentication required"}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"code": 403, "error": "Insufficient permissions"}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"code": 404, "error": "Resource not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"code": 405, "error": "Method not allowed"}), 405

    @app.errorhandler(409)
    def conflict(e):
        msg = getattr(e, "description", None) or str(e)
        return jsonify({"code": 409, "error": msg}), 409

    @app.errorhandler(413)
    def request_entity_too_large(e):
        return jsonify({"code": 413, "error": "File too large. Max 10MB allowed."}), 413

    @app.errorhandler(422)
    def unprocessable(e):
        details = getattr(e, "description", None)
        return jsonify({"code": 422, "error": "Validation error", "details": details}), 422

    @app.errorhandler(429)
    def too_many_requests(e):
        return jsonify({"code": 429, "error": "Rate limit exceeded. Try again later."}), 429

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.exception("Internal server error")
        return jsonify({"code": 500, "error": "An unexpected error occurred."}), 500

    # ── SQLAlchemy error handlers ────────────────────────────────
    @app.errorhandler(IntegrityError)
    def integrity_error(e):
        from app.extensions import db
        db.session.rollback()
        return jsonify({"code": 409, "error": "A conflicting record already exists."}), 409

    @app.errorhandler(OperationalError)
    def operational_error(e):
        return jsonify({"code": 503, "error": "Database unavailable"}), 503
