"""
Vantage — Pipeline Stage 1: Parser
Responsibility: Accept a file path or object and return a raw, unmodified pandas DataFrame.
Supports: .csv, .xlsx, .xls
PDF parsing is currently a generic stub (pdfplumber) to be fleshed out if required.
"""
import os
import pandas as pd
from typing import Tuple, List, BinaryIO

class ParserError(Exception):
    """Custom exception for T-Sheet file parsing failures."""
    pass

def parse_file(file_obj: BinaryIO, filename: str) -> Tuple[pd.DataFrame, List[str]]:
    """
    Parses an uploaded T-Sheet file into a raw pandas DataFrame.
    
    Args:
        file_obj: The uploaded file object (e.g. from request.files)
        filename: The original filename to detect the extension
        
    Returns:
        (DataFrame, list of column names)
        
    Raises:
        ParserError: If the file format is unsupported or parsing fails.
    """
    ext = os.path.splitext(filename)[1].lower()
    
    try:
        if ext == '.csv':
            df = pd.read_csv(file_obj)
        elif ext in ['.xlsx', '.xls']:
            # MGIT T-Sheets: rows 1-4 are institution/exam metadata; row 5 is the column header.
            # Read with header=4 (0-indexed) to treat row 5 as column names.
            # Fall back to default (header=0) if the result has zero usable columns.
            df = pd.read_excel(file_obj, engine='openpyxl', header=4)
            # Sanity check: if every column looks like "Unnamed_N", the file has no row-4 headers
            # (e.g. a plain CSV-style xlsx), so re-read from the top.
            if all(str(c).startswith("Unnamed") for c in df.columns):
                file_obj.seek(0)
                df = pd.read_excel(file_obj, engine='openpyxl')
        elif ext == '.pdf':
            # Basic stub for pdf parsing. Real implementation would use pdfplumber
            # to extract tables securely.
            raise ParserError("PDF parsing is not yet fully implemented. Please use CSV or Excel.")
        else:
            raise ParserError(f"Unsupported file type: {ext}. Expected .csv, .xlsx, or .xls")
            
    except Exception as e:
        if isinstance(e, ParserError):
            raise
        raise ParserError(f"Failed to read file: {e}")

    if df.empty:
        raise ParserError("The uploaded file contains no data.")

    # Clean headers: strip whitespace
    df.columns = [str(col).strip() for col in df.columns]
    
    # Optional: Fill empty column names with a placeholder to prevent alignment issues
    df.columns = [col if col else f"Unnamed_{i}" for i, col in enumerate(df.columns)]
    
    return df, df.columns.tolist()
