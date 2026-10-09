"""
Tests for src/normalizer.py. No real network/API calls are made - the
google-genai client is monkeypatched where needed.

Per scraper-team-minimum-spec.md ("Collect exactly as shown... Never
invent a time or timezone"), date parsing here must preserve only what was
actually present in the source string.
"""
import pytest

import src.normalizer as normalizer


@pytest.fixture(autouse=True)
def _reset_client_cache(monkeypatch):
    monkeypatch.setattr(normalizer, "_client", None)
    monkeypatch.setattr(normalizer, "_client_init_attempted", False)


def test_clean_text_collapses_whitespace_and_strips():
    assert normalizer.clean_text("  Foo   Bar\n") == "Foo Bar"


def test_clean_text_none_and_empty():
    assert normalizer.clean_text(None) is None
    assert normalizer.clean_text("   ") is None


def test_parse_date_deterministic_date_only_source_stays_date_only():
    # No time-of-day in the source - must NOT be padded with a fabricated
    # 00:00:00.
    assert normalizer.parse_date_deterministic("1/5/2026") == "2026-01-05"
    assert normalizer.parse_date_deterministic("January 5, 2026") == "2026-01-05"


def test_parse_date_deterministic_preserves_explicit_time():
    result = normalizer.parse_date_deterministic("January 5, 2026 3:00 PM")
    assert result == "2026-01-05T15:00:00"


def test_parse_date_deterministic_preserves_explicit_offset():
    result = normalizer.parse_date_deterministic("2026-01-05T15:00:00-05:00")
    assert result == "2026-01-05T15:00:00-05:00"


def test_parse_date_deterministic_none_and_empty_and_unparseable():
    assert normalizer.parse_date_deterministic(None) is None
    assert normalizer.parse_date_deterministic("") is None
    assert normalizer.parse_date_deterministic("not a date at all !!!") is None


def test_parse_date_with_fallback_uses_python_first_no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Parseable by dateutil alone - should succeed without ever touching Gemini.
    result = normalizer.parse_date_with_fallback("March 3, 2026")
    assert result == "2026-03-03"


def test_parse_date_with_fallback_returns_none_when_unparseable_and_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = normalizer.parse_date_with_fallback("gibberish")
    assert result is None


def test_parse_date_with_fallback_calls_gemini_only_when_python_fails(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []

    class FakeResponse:
        text = "2026-02-14"

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(normalizer, "_get_client", lambda: FakeClient())

    # dateutil-parseable input should never reach the fake Gemini client.
    normalizer.parse_date_with_fallback("2026-01-01")
    assert calls == []

    # Ambiguous/free-text input falls through to Gemini.
    result = normalizer.parse_date_with_fallback("the second Friday of Feb 2026")
    assert len(calls) == 1
    assert result == "2026-02-14"


def test_parse_date_with_fallback_returns_none_on_gemini_null_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class FakeResponse:
        text = "null"

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(normalizer, "_get_client", lambda: FakeClient())
    assert normalizer.parse_date_with_fallback("complete gibberish") is None


def test_parse_date_with_fallback_rejects_gemini_response_that_isnt_a_real_date(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class FakeResponse:
        text = "definitely not a date"

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(normalizer, "_get_client", lambda: FakeClient())
    assert normalizer.parse_date_with_fallback("complete gibberish") is None


def test_parse_date_with_fallback_returns_none_on_api_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("boom")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(normalizer, "_get_client", lambda: FakeClient())
    assert normalizer.parse_date_with_fallback("still gibberish") is None
