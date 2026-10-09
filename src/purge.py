"""
Automated retention enforcement, per scraper-team-minimum-spec.md's
"Three-month retention" section: "Do not retain copies beyond that period
unless the project owner gives written direction."

Scope: this purges the raw captured CONTENT (files in src/object_storage.py
plus the raw_artifact row that indexes them) once their retained_until has
passed. It does not delete source_record / source_record_version rows -
per the data dictionary those keep their own retained_until/purge_status
too, but the spec explicitly says "The receiving team performs the final
removal process" for the higher-level records; this job only owns the
narrower, unambiguous piece - the actual stored file content.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse
from urllib.request import url2pathname


def _local_path_for_file_uri(storage_uri: str) -> Path | None:
    parsed = urlparse(storage_uri)
    if parsed.scheme != "file":
        return None
    return Path(url2pathname(parsed.path))


def purge_expired_artifacts(conn) -> Dict[str, object]:
    """Find raw_artifact rows past their retained_until with purge_status
    still 'active', delete the underlying stored file (best-effort - a
    missing file is not an error, since the goal is already satisfied),
    and mark the row purged. Commits as it goes so a failure partway
    through doesn't lose progress already made.

    Returns {"purged": <count>, "errors": [<per-row error strings>]}.
    """
    purged = 0
    errors = []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, storage_uri
            FROM procurement.raw_artifact
            WHERE purge_status = 'active' AND retained_until < now()
            """
        )
        rows = cur.fetchall()

    for artifact_id, storage_uri in rows:
        try:
            if storage_uri:
                local_path = _local_path_for_file_uri(storage_uri)
                if local_path is not None and local_path.exists():
                    local_path.unlink()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE procurement.raw_artifact
                    SET purge_status = 'purged', purged_at = now()
                    WHERE id = %s
                    """,
                    (artifact_id,),
                )
            conn.commit()
            purged += 1
        except Exception as e:
            conn.rollback()
            errors.append(f"{artifact_id}: {e}")

    return {"purged": purged, "errors": errors}
