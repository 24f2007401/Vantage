"""Recruiter blueprint — /api/recruiter/*"""
from flask import Blueprint

recruiter_bp = Blueprint("recruiter", __name__)

from app.recruiter import routes  # noqa: E402, F401
