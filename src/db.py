"""
Postgres persistence for scraped records, scoped to the "Source collection
and retention" tables in postgresql-data-dictionary.md: source_system,
source_endpoint, crawl_run, source_record, source_record_version,
raw_artifact.

Everything past that section of the data dictionary (opportunity dedup,
taxonomy/classification, business matching, awards, embeddings) is
downstream processing owned by other teams per scraper-team-minimum-spec.md
("This is a data-collection assignment only. Other teams handle all
processing after delivery.") and is intentionally not written here.

This is an ADDITIONAL delivery path alongside the existing JSONL/JSON file
output (src/storage.py) - it doesn't replace it. Connects via the
DATABASE_URL environment variable; see db/bootstrap.sql and db/schema.sql
for one-time setup.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import psycopg2
import psycopg2.extras
from dateutil.parser import isoparse
from dateutil.relativedelta import relativedelta

from src.storage import DeliveryRecord

RETENTION_MONTHS = 3

_CONTENT_TYPE_TO_ARTIFACT_KIND = {
    "text/html": "html",
    "text/plain": "text",
    "application/json": "json",
    "application/xml": "xml",
    "application/pdf": "pdf",
}


def get_connection(dsn: Optional[str] = None):
    """Open a new connection using DATABASE_URL (or an explicit `dsn`).

    Raises RuntimeError with a clear message if neither is available,
    rather than a confusing KeyError deep in psycopg2.
    """
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set. See db/bootstrap.sql and db/schema.sql "
            "for one-time setup, then export DATABASE_URL=postgresql://..."
        )
    return psycopg2.connect(dsn)


def _retained_until(observed_at: datetime) -> datetime:
    return observed_at + relativedelta(months=RETENTION_MONTHS)


def ensure_source_system(
    conn,
    *,
    name: str,
    base_url: str,
    source_type: str = "portal",
    access_method: str = "browser_automation",
    default_timezone: Optional[str] = None,
) -> str:
    """Insert or fetch the source_system row for (name, base_url). Returns its id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO procurement.source_system
                (name, base_url, source_type, access_method, default_timezone)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name, base_url) DO UPDATE SET
                updated_at = now()
            RETURNING id
            """,
            (name, base_url, source_type, access_method, default_timezone),
        )
        return cur.fetchone()[0]


def ensure_source_endpoint(
    conn,
    *,
    source_system_id: str,
    endpoint_url: str,
    endpoint_kind: str = "listing",
) -> str:
    """Insert or fetch the source_endpoint row for this system+URL. Returns its id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO procurement.source_endpoint
                (source_system_id, endpoint_url, endpoint_kind)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_system_id, endpoint_url) DO UPDATE SET
                endpoint_kind = EXCLUDED.endpoint_kind
            RETURNING id
            """,
            (source_system_id, endpoint_url, endpoint_kind),
        )
        return cur.fetchone()[0]


def start_crawl_run(conn, *, source_endpoint_id: str, scraper_version: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO procurement.crawl_run (source_endpoint_id, scraper_version, status)
            VALUES (%s, %s, 'running')
            RETURNING id
            """,
            (source_endpoint_id, scraper_version),
        )
        return cur.fetchone()[0]


def complete_crawl_run(
    conn,
    *,
    crawl_run_id: str,
    status: str,
    records_found: int,
    error_summary: Optional[str] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE procurement.crawl_run
            SET completed_at = now(), status = %s, records_found = %s, error_summary = %s
            WHERE id = %s
            """,
            (status, records_found, error_summary, crawl_run_id),
        )


def _upsert_source_record(conn, *, source_system_id: str, record: DeliveryRecord) -> str:
    external_id = record.source.source_opportunity_id
    canonical_url = record.source.opportunity_url
    fallback_fingerprint = None
    if not external_id:
        fallback_fingerprint = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()

    observed_at = isoparse(record.observed_at)
    retained_until = _retained_until(observed_at)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO procurement.source_record
                (source_system_id, external_id, fallback_fingerprint, canonical_url,
                 access_level, first_seen_at, last_seen_at, retained_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_system_id, external_id) WHERE external_id IS NOT NULL
            DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                canonical_url = EXCLUDED.canonical_url,
                access_level = EXCLUDED.access_level
            RETURNING id
            """,
            (
                source_system_id,
                external_id,
                fallback_fingerprint,
                canonical_url,
                record.source.access_level,
                observed_at,
                observed_at,
                retained_until,
            ),
        )
        return cur.fetchone()[0]


def _insert_source_record_version(
    conn, *, source_record_id: str, crawl_run_id: str, record: DeliveryRecord
) -> Optional[str]:
    observed_at = isoparse(record.observed_at)
    retained_until = _retained_until(observed_at)
    content_hash = record.raw_capture.sha256 or hashlib.sha256(
        record.model_dump_json().encode("utf-8")
    ).hexdigest()
    candidate_fields = {
        "opportunity": record.opportunity.model_dump(),
        "documents": [d.model_dump() for d in record.documents],
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO procurement.source_record_version
                (source_record_id, crawl_run_id, observed_at, content_hash,
                 raw_payload_uri, candidate_fields, parser_version, retained_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_record_id, content_hash) DO NOTHING
            RETURNING id
            """,
            (
                source_record_id,
                crawl_run_id,
                observed_at,
                content_hash,
                record.raw_capture.storage_uri,
                psycopg2.extras.Json(candidate_fields),
                record.crawl.parser_version,
                retained_until,
            ),
        )
        row = cur.fetchone()
        return row[0] if row else None


def _insert_raw_artifact(conn, *, source_record_version_id: str, record: DeliveryRecord) -> None:
    observed_at = isoparse(record.observed_at)
    retained_until = _retained_until(observed_at)
    artifact_kind = _CONTENT_TYPE_TO_ARTIFACT_KIND.get(
        record.raw_capture.content_type, "other"
    )
    checksum = record.raw_capture.sha256 or hashlib.sha256(b"").hexdigest()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO procurement.raw_artifact
                (source_record_version_id, artifact_kind, storage_uri, source_url,
                 mime_type, checksum_sha256, retrieved_at, retained_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                source_record_version_id,
                artifact_kind,
                record.raw_capture.storage_uri,
                record.source.opportunity_url,
                record.raw_capture.content_type,
                checksum,
                observed_at,
                retained_until,
            ),
        )


def persist_delivery_records(
    conn,
    records: List[DeliveryRecord],
    *,
    source_name: str,
    base_url: str,
    endpoint_url: str,
    endpoint_kind: str = "listing",
    scraper_version: str,
    default_timezone: Optional[str] = None,
) -> str:
    """Persist a batch of DeliveryRecord objects from one scrape run.

    Ensures the source_system/source_endpoint rows exist, opens one
    crawl_run for the whole batch, and upserts each record's source_record
    / source_record_version / raw_artifact rows. Commits once at the end;
    the caller owns the connection's lifecycle (open/close).

    Returns the crawl_run id.
    """
    source_system_id = ensure_source_system(
        conn, name=source_name, base_url=base_url, default_timezone=default_timezone
    )
    source_endpoint_id = ensure_source_endpoint(
        conn,
        source_system_id=source_system_id,
        endpoint_url=endpoint_url,
        endpoint_kind=endpoint_kind,
    )
    crawl_run_id = start_crawl_run(
        conn, source_endpoint_id=source_endpoint_id, scraper_version=scraper_version
    )
    # Commit setup + crawl_run separately so the run is recorded even if a
    # later record fails - a failed INSERT aborts the whole surrounding
    # Postgres transaction, so anything not yet committed would be lost too.
    conn.commit()

    record_errors: List[str] = []
    for record in records:
        # Each record commits (or rolls back) on its own, so one bad record
        # can't abort the ones already committed before it or the ones
        # still to come.
        try:
            source_record_id = _upsert_source_record(
                conn, source_system_id=source_system_id, record=record
            )
            version_id = _insert_source_record_version(
                conn,
                source_record_id=source_record_id,
                crawl_run_id=crawl_run_id,
                record=record,
            )
            # version_id is None when this exact content was already stored
            # (ON CONFLICT DO NOTHING on content_hash) - nothing new to
            # attach a raw_artifact row to in that case.
            if version_id is not None:
                _insert_raw_artifact(conn, source_record_version_id=version_id, record=record)
            conn.commit()
        except Exception as e:
            conn.rollback()
            record_errors.append(f"{record.source.opportunity_url}: {e}")

    if not record_errors:
        status = "ok"
    elif len(record_errors) < len(records):
        status = "partial"
    else:
        status = "error"

    complete_crawl_run(
        conn,
        crawl_run_id=crawl_run_id,
        status=status,
        records_found=len(records) - len(record_errors),
        error_summary="; ".join(record_errors[:5])[:500] if record_errors else None,
    )
    conn.commit()

    return crawl_run_id
