"""
Data-formatting helpers for scraped procurement records.

Formatting is done in Python first (whitespace/casing cleanup, date parsing
via dateutil) since that's deterministic, free, and fast. Gemini is only
called as a last resort, per-field, when Python's deterministic parsing
can't confidently make sense of a raw value - never as a first pass over
the whole record. If GEMINI_API_KEY is unset, the client fails to
initialize, or the API call errors, callers get None back and keep the
original raw value untouched.

Per scraper-team-minimum-spec: "Collect exactly as shown... Never invent a
time or timezone." So date parsing here only reformats components that were
actually present in the source string - a source value with no time of day
comes back as a plain `YYYY-MM-DD` date string, never padded with a
fabricated 00:00:00, and no timezone offset is added unless the source text
itself carried one.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

from dateutil import parser as dateutil_parser

GEMINI_MODEL = "gemini-2.5-flash"

_client = None
_client_init_attempted = False

_WHITESPACE_RE = re.compile(r"\s+")

# Two implausible, distinct sentinel datetimes. If dateutil fills in the
# same default for a component under both sentinels, that component wasn't
# actually present in the source string. See parse_date_deterministic.
_SENTINEL_A = datetime(1, 1, 1, 1, 1, 1)
_SENTINEL_B = datetime(2, 2, 2, 2, 2, 2)


def clean_text(value: Optional[str]) -> Optional[str]:
    """Deterministic text cleanup: collapse whitespace, strip. No LLM call."""
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value).strip()
    return cleaned or None


def parse_date_deterministic(raw: Optional[str]) -> Optional[str]:
    """Try to parse a raw date string using dateutil alone, preserving only
    the components actually present in `raw`.

    Returns None if `raw` is empty, dateutil can't parse it, or no explicit
    calendar date could be identified in it (we don't fall back to
    "today"). Returns a bare `YYYY-MM-DD` when no time-of-day was present,
    or a full ISO 8601 datetime (with offset, only if one was in the
    source) when a time was present.
    """
    raw = clean_text(raw)
    if not raw:
        return None
    try:
        dt_a = dateutil_parser.parse(raw, default=_SENTINEL_A)
        dt_b = dateutil_parser.parse(raw, default=_SENTINEL_B)
    except (ValueError, OverflowError, TypeError):
        return None

    # No explicit date component -> the two sentinel dates leaked through
    # differently and don't agree; that means dateutil could only guess a
    # date, so we don't invent one.
    if dt_a.date() != dt_b.date():
        return None

    def _specified(value_a, value_b, sentinel_a, sentinel_b) -> bool:
        # A component is "explicit in the source" only if it did NOT just
        # fall through from our two (deliberately different) defaults.
        return not (value_a == sentinel_a and value_b == sentinel_b)

    hour_given = _specified(dt_a.hour, dt_b.hour, _SENTINEL_A.hour, _SENTINEL_B.hour)
    minute_given = _specified(dt_a.minute, dt_b.minute, _SENTINEL_A.minute, _SENTINEL_B.minute)
    second_given = _specified(dt_a.second, dt_b.second, _SENTINEL_A.second, _SENTINEL_B.second)

    if not (hour_given or minute_given):
        # No real time-of-day in the source - report a bare date, never a
        # fabricated 00:00:00.
        return dt_a.date().isoformat()

    result = dt_a.replace(second=dt_a.second if second_given else 0, microsecond=0)
    return result.isoformat()


def _get_client():
    """Lazily construct a google-genai Client from GEMINI_API_KEY.

    Cached across calls; returns None (and stays None) once initialization
    has been attempted and failed, so repeated calls in a scrape loop don't
    retry a doomed import/auth on every record.
    """
    global _client, _client_init_attempted
    if _client_init_attempted:
        return _client
    _client_init_attempted = True

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        _client = genai.Client(api_key=api_key)
    except Exception:
        _client = None
    return _client


def parse_date_with_fallback(raw: Optional[str]) -> Optional[str]:
    """Parse a raw date string, trying Python (dateutil) first and only
    calling Gemini if that fails. This is the expected entry point for
    scrapers - `parse_date_deterministic` alone skips the LLM fallback
    entirely, useful for tests or when a Gemini call is undesirable.
    """
    parsed = parse_date_deterministic(raw)
    if parsed is not None:
        return parsed

    raw = clean_text(raw)
    if not raw:
        return None

    client = _get_client()
    if client is None:
        return None

    try:
        from google.genai import types
    except Exception:
        return None

    prompt = (
        "Reformat this date/time string, extracted from a procurement "
        "website, as ISO 8601 - but preserve only what is explicitly "
        "present. If no time-of-day is stated, respond with just a "
        "YYYY-MM-DD date, do not add a time. If no timezone/offset is "
        "stated, do not add one. Never guess or invent a value that is "
        "not in the input. If the string cannot be confidently "
        "interpreted as a real calendar date at all, respond with "
        "exactly: null\n\n"
        f"Input: {raw!r}"
    )
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            # No tools/function-calling used here - disabling AFC only
            # silences the SDK's "use Chat.send_message instead" nag.
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
            ),
        )
        text = (getattr(response, "text", None) or "").strip()
    except Exception:
        return None

    if not text or text.lower() == "null":
        return None
    # Re-validate Gemini's answer deterministically rather than trusting it
    # blindly - it must itself be a parseable ISO-ish value.
    return parse_date_deterministic(text)
