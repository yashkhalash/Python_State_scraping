"""
Approved object-storage location for raw captures and downloaded documents,
per scraper-team-minimum-spec.md ("raw_capture: Required for a successful
scrape. Save the HTML/JSON/API response and provide its approved
storage URI plus SHA-256" and the "PDF and document handling" section).

This is a local-filesystem implementation, configured via
OBJECT_STORAGE_DIR (default: data/object_store). storage_uri values are
file:// URIs pointing into that directory. Swapping to a real object store
(S3, GCS, etc.) later only means replacing this module - callers only see
save_text()/download_document() and a storage_uri string, never a path.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

DEFAULT_STORAGE_DIR = "data/object_store"


def _storage_root() -> Path:
    return Path(os.environ.get("OBJECT_STORAGE_DIR", DEFAULT_STORAGE_DIR))


def _write_bytes(content: bytes, *, prefix: str, extension: str) -> tuple[str, str, int]:
    """Write `content` under <storage_root>/<prefix>/<sha256><extension>.
    Returns (storage_uri, sha256_hex, byte_size). Content-addressed, so
    writing the same bytes twice is a cheap no-op on the second call."""
    sha256 = hashlib.sha256(content).hexdigest()
    root = _storage_root() / prefix
    root.mkdir(parents=True, exist_ok=True)
    file_path = root / f"{sha256}{extension}"
    if not file_path.exists():
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(file_path)
    storage_uri = file_path.resolve().as_uri()
    return storage_uri, sha256, len(content)


def save_text(content: str, *, prefix: str, extension: str = ".html") -> tuple[str, str]:
    """Save raw page text (HTML/markdown/JSON) to the approved storage
    location. Returns (storage_uri, sha256_hex)."""
    storage_uri, sha256, _ = _write_bytes(content.encode("utf-8"), prefix=prefix, extension=extension)
    return storage_uri, sha256


def _guess_extension(url: str, content_type: Optional[str]) -> str:
    path = urlparse(url).path
    if "." in path.rsplit("/", 1)[-1]:
        return "." + path.rsplit(".", 1)[-1][:10]
    if content_type:
        if "pdf" in content_type:
            return ".pdf"
        if "html" in content_type:
            return ".html"
    return ".bin"


def download_document(url: str, *, prefix: str = "documents", timeout: float = 30.0) -> dict:
    """Download a document URL unchanged and save it to the approved storage
    location (spec: "download the file unchanged; do not OCR, summarize, or
    edit it"). Never raises - failures come back as a dict the caller can
    turn directly into a DeliveryDocument's download_status/error fields,
    per the spec's "document URL must still be included" rule even when a
    download fails.

    Returns:
        {"download_status": "downloaded", "storage_uri": ..., "sha256": ...,
         "byte_size": ..., "mime_type": ...}
        or
        {"download_status": "failed", "error": "..."}
    """
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
            mime_type = response.headers.get("content-type", "").split(";")[0].strip() or None
    except Exception as e:
        return {"download_status": "failed", "error": f"{type(e).__name__}: {e}"}

    extension = _guess_extension(url, mime_type)
    storage_uri, sha256, byte_size = _write_bytes(content, prefix=prefix, extension=extension)
    return {
        "download_status": "downloaded",
        "storage_uri": storage_uri,
        "sha256": sha256,
        "byte_size": byte_size,
        "mime_type": mime_type,
    }
