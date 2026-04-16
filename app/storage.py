"""
Vantage — Supabase Storage Service
Handles upload / signed-URL / delete for T-Sheet files in the
'tsheet-uploads' bucket.  Falls back to local filesystem when
SUPABASE_SERVICE_ROLE_KEY is not configured (e.g. local dev without key).
"""
import hashlib
import io
import os
from flask import current_app
from werkzeug.utils import secure_filename

BUCKET = "tsheet-uploads"


def _client():
    """Return a live Supabase client using service-role credentials."""
    from supabase import create_client
    url = current_app.config.get("SUPABASE_URL", "")
    key = current_app.config.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to use remote storage."
        )
    return create_client(url, key)


def _is_configured() -> bool:
    return bool(
        current_app.config.get("SUPABASE_URL")
        and current_app.config.get("SUPABASE_SERVICE_ROLE_KEY")
    )


# ── Local filesystem fallback ────────────────────────────────────────

_LOCAL_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "uploads"
)


def _upload_local(file_bytes: bytes, safe_name: str, batch_id: str) -> dict:
    os.makedirs(_LOCAL_UPLOAD_DIR, exist_ok=True)
    storage_path = os.path.join(_LOCAL_UPLOAD_DIR, f"{batch_id}_{safe_name}")
    with open(storage_path, "wb") as fh:
        fh.write(file_bytes)
    return {
        "storage_path": storage_path,
        "file_hash_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "file_size_bytes": len(file_bytes),
        "original_filename": safe_name,
    }


def _local_bytes(storage_path: str) -> bytes:
    with open(storage_path, "rb") as fh:
        return fh.read()


# ── Public API ───────────────────────────────────────────────────────

def upload_tsheet(file_bytes: bytes, original_filename: str, batch_id: str) -> dict:
    """
    Upload a T-Sheet file to Supabase Storage (or local FS fallback).

    Storage path: tsheets/{batch_id}/{safe_filename}

    Returns:
      {
        "storage_path": str,
        "file_hash_sha256": str,
        "file_size_bytes": int,
        "original_filename": str,
      }
    """
    safe_name = secure_filename(original_filename)
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    if not _is_configured():
        current_app.logger.warning(
            "[Storage] SUPABASE_SERVICE_ROLE_KEY not set — falling back to local filesystem."
        )
        return _upload_local(file_bytes, safe_name, batch_id)

    storage_path = f"tsheets/{batch_id}/{safe_name}"
    _client().storage.from_(BUCKET).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": _mime_type(safe_name), "upsert": "false"},
    )
    return {
        "storage_path": storage_path,
        "file_hash_sha256": file_hash,
        "file_size_bytes": len(file_bytes),
        "original_filename": safe_name,
    }


def download_tsheet_bytes(storage_path: str) -> bytes:
    """Return raw bytes of a stored T-Sheet (from Supabase or local FS fallback)."""
    if not _is_configured() or not storage_path.startswith("tsheets/"):
        # Local fallback — storage_path is an absolute filesystem path
        return _local_bytes(storage_path)

    return _client().storage.from_(BUCKET).download(storage_path)


def get_tsheet_signed_url(storage_path: str, expires_in: int = 3600) -> str:
    """Return a signed download URL valid for expires_in seconds."""
    if not _is_configured():
        raise RuntimeError("Supabase storage not configured — no signed URL available.")

    res = _client().storage.from_(BUCKET).create_signed_url(storage_path, expires_in)
    # SDK returns {"signedURL": ...} or {"signedUrl": ...} depending on version
    return res.get("signedURL") or res.get("signedUrl") or res["data"]["signedUrl"]


def delete_tsheet(storage_path: str) -> None:
    """Delete a stored T-Sheet file."""
    if not _is_configured() or not storage_path.startswith("tsheets/"):
        if os.path.exists(storage_path):
            os.remove(storage_path)
        return
    _client().storage.from_(BUCKET).remove([storage_path])


def _mime_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    return {
        "csv":  "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls":  "application/vnd.ms-excel",
        "pdf":  "application/pdf",
    }.get(ext, "application/octet-stream")
