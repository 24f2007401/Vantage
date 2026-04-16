"""
Vantage — Input Validators
CGPA range checks, roll-number format validation, etc.
"""
import re


def validate_cgpa(value):
    """
    Validate that CGPA is a float in [0.0, 10.0].

    Returns:
        float — the validated CGPA value.

    Raises:
        ValueError: If out of range or not a number.
    """
    try:
        cgpa = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid CGPA value: {value!r}")
    if not (0.0 <= cgpa <= 10.0):
        raise ValueError(f"CGPA must be between 0.0 and 10.0, got {cgpa}")
    return cgpa


def validate_roll_number(value):
    """
    Validate MGIT roll-number format (e.g. 24261A0502).

    Pattern: 2 digits (year) + 3 digits (college) + 1 letter + 4 digits

    Returns:
        str — the uppercased, validated roll number.

    Raises:
        ValueError: If format doesn't match.
    """
    pattern = r"^\d{5}[A-Za-z]\d{4}$"
    if not isinstance(value, str) or not re.match(pattern, value.strip()):
        raise ValueError(
            f"Invalid roll number format: {value!r}. "
            "Expected pattern like 24261A0502."
        )
    return value.strip().upper()
