"""
Tests for src/db.py. No real Postgres connection is used - conn/cursor are
mocked so these run without a live database.
"""
from unittest.mock import MagicMock, call

import pytest

import src.db as db
from src.storage import (
    CrawlInfo,
    DeliveryOpportunity,
    DeliveryRecord,
    DeliverySource,
    RawCapture,
)


def _make_record(opportunity_url="https://portal.example/bid/1", source_opportunity_id="EPD-1", sha256="abc123"):
    return DeliveryRecord(
        observed_at="2026-08-01T12:00:00Z",
        source=DeliverySource(
            name="Example Portal",
            base_url="https://portal.example",
            opportunity_url=opportunity_url,
            source_opportunity_id=source_opportunity_id,
            access_level="public",
        ),
        opportunity=DeliveryOpportunity(title="Road Salt Supply"),
        documents=[],
        raw_capture=RawCapture(content_type="text/html", sha256=sha256),
        crawl=CrawlInfo(run_id="test-run", parser_version="test-v1"),
    )


class _FakeCursorCM:
    """Mimics `with conn.cursor() as cur: ...` returning a shared mock cursor."""

    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, *exc):
        return False


def _make_conn(fetchone_results):
    """conn.cursor() always returns the same mock cursor; cur.fetchone()
    returns successive values from `fetchone_results` on each call."""
    cursor = MagicMock()
    cursor.fetchone.side_effect = fetchone_results
    conn = MagicMock()
    conn.cursor.side_effect = lambda: _FakeCursorCM(cursor)
    return conn, cursor


def test_get_connection_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        db.get_connection()


def test_retained_until_is_three_calendar_months_later():
    from datetime import datetime, timezone
    observed = datetime(2026, 1, 31, tzinfo=timezone.utc)
    result = db._retained_until(observed)
    assert result.year == 2026
    assert result.month == 4
    # relativedelta clamps Jan 31 + 3 months to Apr 30 (no Apr 31).
    assert result.day == 30


def test_persist_delivery_records_happy_path():
    conn, cursor = _make_conn(
        fetchone_results=[
            ("system-1",),   # ensure_source_system
            ("endpoint-1",),  # ensure_source_endpoint
            ("run-1",),      # start_crawl_run
            ("record-1",),   # _upsert_source_record
            ("version-1",),  # _insert_source_record_version
        ]
    )
    records = [_make_record()]

    crawl_run_id = db.persist_delivery_records(
        conn,
        records,
        source_name="Example Portal",
        base_url="https://portal.example",
        endpoint_url="https://portal.example/bids",
        scraper_version="test-v1",
    )

    assert crawl_run_id == "run-1"
    # setup commit + per-record commit + final commit = 3
    assert conn.commit.call_count == 3
    assert conn.rollback.call_count == 0

    # complete_crawl_run's UPDATE should report status "ok", no errors.
    update_call = [c for c in cursor.execute.call_args_list if "UPDATE procurement.crawl_run" in c.args[0]]
    assert len(update_call) == 1
    params = update_call[0].args[1]
    assert params[0] == "ok"
    assert params[1] == 1  # records_found


def test_persist_delivery_records_skips_raw_artifact_when_version_already_exists():
    conn, cursor = _make_conn(
        fetchone_results=[
            ("system-1",),
            ("endpoint-1",),
            ("run-1",),
            ("record-1",),
            None,  # ON CONFLICT DO NOTHING - version already existed
        ]
    )
    records = [_make_record()]

    db.persist_delivery_records(
        conn,
        records,
        source_name="Example Portal",
        base_url="https://portal.example",
        endpoint_url="https://portal.example/bids",
        scraper_version="test-v1",
    )

    artifact_calls = [c for c in cursor.execute.call_args_list if "raw_artifact" in c.args[0]]
    assert artifact_calls == []


def test_persist_delivery_records_marks_partial_on_one_bad_record():
    conn, cursor = _make_conn(
        fetchone_results=[
            ("system-1",),
            ("endpoint-1",),
            ("run-1",),
            ("record-1",),  # record 1's upsert succeeds
            ("version-1",),
        ]
    )
    # Make _upsert_source_record raise on the second record specifically by
    # having its INSERT execute() throw.
    original_execute = cursor.execute

    call_count = {"n": 0}

    def flaky_execute(query, params=None):
        if "INSERT INTO procurement.source_record" in query and "source_record_version" not in query:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated db error")
        return original_execute(query, params)

    cursor.execute = MagicMock(side_effect=flaky_execute)

    records = [_make_record(opportunity_url="https://portal.example/bid/1", source_opportunity_id="EPD-1"),
               _make_record(opportunity_url="https://portal.example/bid/2", source_opportunity_id="EPD-2")]

    db.persist_delivery_records(
        conn,
        records,
        source_name="Example Portal",
        base_url="https://portal.example",
        endpoint_url="https://portal.example/bids",
        scraper_version="test-v1",
    )

    assert conn.rollback.call_count == 1

    update_call = [c for c in cursor.execute.call_args_list if "UPDATE procurement.crawl_run" in c.args[0]]
    assert len(update_call) == 1
    params = update_call[0].args[1]
    assert params[0] == "partial"
    assert params[1] == 1  # only 1 of 2 records succeeded
