"""
Tests for src/purge.py. No real Postgres connection - conn/cursor are
mocked. Actual file deletion is exercised against real temp files (no
network involved).
"""
from unittest.mock import MagicMock

from src.purge import _local_path_for_file_uri, purge_expired_artifacts


class _FakeCursorCM:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, *exc):
        return False


def _make_conn(select_rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = select_rows
    conn = MagicMock()
    conn.cursor.side_effect = lambda: _FakeCursorCM(cursor)
    return conn, cursor


def test_local_path_for_file_uri_roundtrips(tmp_path):
    target = tmp_path / "raw_captures" / "abc123.html"
    uri = target.as_uri()
    assert _local_path_for_file_uri(uri) == target


def test_local_path_for_file_uri_ignores_non_file_scheme():
    assert _local_path_for_file_uri("https://example.com/foo") is None


def test_purge_expired_artifacts_deletes_file_and_marks_purged(tmp_path):
    target = tmp_path / "expired.html"
    target.write_text("expired content")
    uri = target.as_uri()

    conn, cursor = _make_conn([("artifact-1", uri)])

    result = purge_expired_artifacts(conn)

    assert result == {"purged": 1, "errors": []}
    assert not target.exists()

    update_calls = [c for c in cursor.execute.call_args_list if "UPDATE procurement.raw_artifact" in c.args[0]]
    assert len(update_calls) == 1
    assert update_calls[0].args[1] == ("artifact-1",)
    assert conn.commit.call_count == 1


def test_purge_expired_artifacts_missing_file_is_not_an_error(tmp_path):
    missing_path = tmp_path / "already_gone.html"
    uri = missing_path.as_uri()

    conn, cursor = _make_conn([("artifact-1", uri)])

    result = purge_expired_artifacts(conn)

    assert result == {"purged": 1, "errors": []}


def test_purge_expired_artifacts_continues_after_one_failure(tmp_path):
    good_target = tmp_path / "good.html"
    good_target.write_text("content")
    bad_target = tmp_path / "bad.html"
    bad_target.write_text("content")

    conn, cursor = _make_conn([("artifact-bad", bad_target.as_uri()), ("artifact-good", good_target.as_uri())])

    # Make the UPDATE fail for the first row only, so it never reaches
    # commit for that row - the second row should still be processed.
    original_execute = cursor.execute
    call_count = {"n": 0}

    def flaky_execute(query, params=None):
        if "UPDATE procurement.raw_artifact" in query:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("db error")
        return original_execute(query, params)

    cursor.execute = MagicMock(side_effect=flaky_execute)

    result = purge_expired_artifacts(conn)

    assert result["purged"] == 1
    assert len(result["errors"]) == 1
    assert "artifact-bad" in result["errors"][0]
    assert conn.rollback.call_count == 1
    assert conn.commit.call_count == 1
    # The bad row's file is only deleted before the (failing) UPDATE, so it
    # was removed even though the row itself wasn't marked purged - not
    # ideal, but the row is retried on the next run since purge_status is
    # still 'active' and the file being gone is harmless (unlink on a
    # missing file is treated as success, see the "missing file" test).
    assert not bad_target.exists()
    assert not good_target.exists()
