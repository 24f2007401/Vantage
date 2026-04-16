"""
Vantage — Pipeline Stage 2: Normalizer
Responsibility: Map arbitrary column names to the canonical schema fields based
on the Module 2 parsing rules, and return a list of clean dict records.
"""
import re
import pandas as pd
from typing import List, Dict, Any

# Regex to detect MGIT roll numbers (e.g., 21261A0501)
ROLL_REGEX = re.compile(r"^\d{2}26\d[A-Z]\d{4}$")

# Mapping of canonical field names to possible raw column headers (fuzzy matches)
COLUMN_MAP = {
    "raw_ht_no": ["roll no", "roll number", "ht number", "ht no", "regd no", "regd", "htno", "hall ticket", "h t no"],
    "raw_course_code": ["course code", "subject code", "sub code"],
    "raw_course_name": ["course name", "subject name", "subject"],
    "raw_cie_marks": ["cie", "internal", "mid", "internals"],
    "raw_see_marks": ["see", "external", "end", "externals"],
    "raw_total_marks": ["total", "total marks"],
    "raw_grade_letter": ["grade", "grade letter", "gl"],
    "raw_grade_points": ["points", "grade points", "gp"],
    "raw_credits": ["credits", "credit", "cr"],
}

def _fuzzy_match_column(raw_col: str) -> str:
    """Matches a raw column name to a canonical field name.
    Normalizes punctuation so 'H.T.No.' matches the same as 'ht no'."""
    raw_lower = str(raw_col).strip().lower()
    # Strip dots, hyphens, underscores, extra spaces so "H.T.No." → "ht no"
    import re as _re
    raw_normalized = _re.sub(r'[.\-_]+', ' ', raw_lower).strip()
    raw_normalized = _re.sub(r'\s+', ' ', raw_normalized)
    for canonical_field, keywords in COLUMN_MAP.items():
        for keyword in keywords:
            if keyword in raw_lower or keyword in raw_normalized:
                return canonical_field
    return raw_col  # Return original if no match

def normalize_dataframe(df: pd.DataFrame, source_sheet_name: str = "Sheet1") -> List[Dict[str, Any]]:
    """
    Transforms a raw DataFrame into a list of standardized dictionaries ready for the Staging Model.
    Expected to output row dicts matching the TSheetRecord fields.
    """
    records = []
    
    # 1. Rename columns based on fuzzy matching
    rename_mapping = {}
    for col in df.columns:
        canonical = _fuzzy_match_column(col)
        if canonical != col:
            # Avoid duplicate column assignments if fuzzy map matches multiple
            if canonical not in rename_mapping.values():
                rename_mapping[col] = canonical
    
    df_mapped = df.rename(columns=rename_mapping)
    
    # 2. Iterate and normalize rows
    for idx, row in df_mapped.iterrows():
        # Requires at least a Roll Number (raw_ht_no) AND a Course Code to be a valid grade row
        raw_ht_no = str(row.get("raw_ht_no", "")).strip().upper()
        raw_course_code = str(row.get("raw_course_code", "")).strip().upper()
        
        # Skip empty rows or rows missing core identifiers
        if not raw_ht_no or raw_ht_no == "NAN" or not raw_course_code or raw_course_code == "NAN":
            continue
            
        # Basic validation (warning only, handled during ingestion conflict phase)
        # Note: MgIT Roll number regex matching is strictly for warnings in the logs here, 
        # actual creation is handled inside ingestion.py
        
        record = {
            "raw_ht_no": raw_ht_no,
            "raw_course_code": raw_course_code,
            "raw_course_name": str(row.get("raw_course_name", "")).strip(),
            "raw_cie_marks": str(row.get("raw_cie_marks", "")).strip(),
            "raw_see_marks": str(row.get("raw_see_marks", "")).strip(),
            "raw_total_marks": str(row.get("raw_total_marks", "")).strip(),
            "raw_grade_letter": str(row.get("raw_grade_letter", "")).strip().partition('.')[0] if '.' in str(row.get("raw_grade_letter", "")) else str(row.get("raw_grade_letter", "")).strip(),
            "raw_grade_points": str(row.get("raw_grade_points", "")).strip(),
            "raw_credits": str(row.get("raw_credits", "")).strip(),
            "source_sheet_name": source_sheet_name,
            "source_row_number": idx + 2  # +2 accounts for 0-index and header row
        }
        
        # Clean out "nan" strings that pandas inserts for empty cells
        for k, v in record.items():
            if str(v).lower() == "nan":
                record[k] = None
                
        records.append(record)
        
    return records
