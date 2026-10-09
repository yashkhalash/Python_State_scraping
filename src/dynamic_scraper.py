"""
Fully dynamic scraper: no per-portal selectors at all. Crawl4AI fetches and
renders the page (same stealth/Playwright stack as every other scraper in
this project - see src/crawler.py), then Gemini reads the page content and
extracts structured bid listings directly.

This is a deliberate tradeoff, not a replacement for the selector-based
scrapers in src/scrapers/*.py:

  - Slower and costs tokens per page (an LLM call per listing/detail page).
  - Non-deterministic: output can vary run-to-run and needs validation.
  - But: works against a brand-new/unknown portal with zero selector code,
    so it's meant for onboarding a new state quickly, not for the 6+ portals
    already covered by deterministic scrapers (those stay as-is - swapping
    them to this path would be a pure regression in cost/reliability).

Every extracted record is validated against the existing DeliveryRecord/
DeliveryOpportunity pydantic schema (src/storage.py) before being returned.
Records where Gemini couldn't fill required fields are marked
record_status="partial" (one of the spec's allowed record_status values -
see scraper-team-minimum-spec.md) with the missing fields listed in
crawl.errors, rather than being silently dropped or invented.

Per that spec ("Preserve the source value... Never invent a time or
timezone"), this module never fabricates a value that wasn't on the page:
dates are only reformatted (via src/normalizer.py's parse_date_with_fallback,
Python/dateutil first, Gemini only as a last resort) using whatever
precision the source actually gave - a date-only source value stays
date-only, never padded with an invented time or timezone offset.
source_timezone is left null unless the page itself states one.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from crawl4ai import AsyncWebCrawler
from pydantic import BaseModel, Field

from src.crawler import fetch_page
from src.normalizer import clean_text, parse_date_with_fallback
from src.object_storage import download_document, save_text
from src.robots import is_allowed as robots_allow
from src.storage import (
    CrawlInfo,
    DeliveryDocument,
    DeliveryOpportunity,
    DeliveryRecord,
    DeliverySource,
    RawCapture,
)

PARSER_VERSION = "dynamic-gemini-v1"
GEMINI_MODEL = "gemini-2.5-flash"


class RobotsDisallowedError(Exception):
    """Raised when robots.txt disallows fetching the given URL.
    scraper-team-minimum-spec.md: "Do not bypass... robots restrictions"."""


class ExtractedDocument(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None


class ExtractedOpportunity(BaseModel):
    """Raw fields as Gemini reads them off the page - not yet the final
    DeliveryOpportunity shape. Dates are kept as raw strings here; they're
    parsed to ISO 8601 separately via normalizer.parse_date_with_fallback."""
    title: Optional[str] = None
    agency_name: Optional[str] = None
    solicitation_number: Optional[str] = None
    status: Optional[str] = None
    notice_type: Optional[str] = None
    published_at_raw: Optional[str] = None
    due_at_raw: Optional[str] = None
    source_timezone: Optional[str] = None
    description: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    submission_url: Optional[str] = None
    detail_page_url: Optional[str] = None
    documents: List[ExtractedDocument] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    opportunities: List[ExtractedOpportunity] = Field(default_factory=list)


def _get_client():
    import os
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception:
        return None


def extract_opportunities_from_page(page_content: str, *, page_url: str) -> List[ExtractedOpportunity]:
    """Send rendered page content (markdown/cleaned HTML from Crawl4AI) to
    Gemini and ask it to extract every bid/solicitation listing it can find.
    Returns [] on any failure (no API key, API error, invalid output) - the
    caller treats that the same as "nothing found", never as a crash."""
    client = _get_client()
    if client is None:
        return []

    try:
        from google.genai import types
    except Exception:
        return []

    prompt = (
        "This is the rendered content of a government procurement/bid "
        "opportunities web page. Extract every distinct bid, solicitation, "
        "or contract opportunity listed on it into structured fields. This "
        "is data collection only - do not infer, summarize, or alter "
        "source values, and do not decide whether two listings describe "
        "the same procurement. Only use information present on the page - "
        "never invent a value (including status, agency, category, date, "
        "or timezone) that is not explicitly shown. Leave a field null (or "
        "an empty list for categories/documents) if it isn't present. Keep "
        "raw date strings and category labels exactly as they appear on "
        "the page (do not reformat or translate them yourself) - the "
        "*_raw date fields will be reformatted separately. If a listing "
        "links to its own full detail/view page (as opposed to a direct "
        "submission link), put that URL in detail_page_url.\n\n"
        f"Page URL: {page_url}\n\nPage content:\n{page_content}"
    )
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ExtractionResult,
                # No tools/function-calling used here - disabling AFC only
                # silences the SDK's "use Chat.send_message instead" nag,
                # which doesn't apply since we never call it in a loop.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
    except Exception:
        return []

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ExtractionResult):
        return parsed.opportunities

    text = getattr(response, "text", None)
    if not text:
        return []
    try:
        return ExtractionResult.model_validate_json(text).opportunities
    except Exception:
        return []


def _merge_opportunity(
    listing: ExtractedOpportunity, detail: ExtractedOpportunity
) -> ExtractedOpportunity:
    """Combine a listing-page extraction with its detail-page extraction.
    Detail-page values win where present (the detail page is generally more
    complete/authoritative); listing-page values are kept as a fallback for
    anything the detail page didn't surface. Documents/categories are
    unioned rather than one replacing the other, since either page might
    show items the other doesn't."""
    merged = listing.model_copy()
    for field in (
        "title", "agency_name", "solicitation_number", "status", "notice_type",
        "published_at_raw", "due_at_raw", "source_timezone", "description",
        "submission_url",
    ):
        detail_value = getattr(detail, field)
        if detail_value:
            setattr(merged, field, detail_value)

    seen_categories = {c.strip().lower() for c in merged.categories}
    for c in detail.categories:
        if c and c.strip().lower() not in seen_categories:
            merged.categories.append(c)
            seen_categories.add(c.strip().lower())

    seen_doc_urls = {d.url for d in merged.documents if d.url}
    for d in detail.documents:
        if d.url and d.url not in seen_doc_urls:
            merged.documents.append(d)
            seen_doc_urls.add(d.url)

    return merged


async def _download_documents(raw_documents: List[ExtractedDocument]) -> List[DeliveryDocument]:
    """Download every linked document to the approved storage location
    (scraper-team-minimum-spec.md: "download the file unchanged"). A failed
    download still keeps the document with its URL and a 'failed' status,
    per the spec's "document URL must still be included" rule - it never
    drops a document just because the download didn't work."""
    documents: List[DeliveryDocument] = []
    for d in raw_documents:
        if not d.url:
            continue
        title = clean_text(d.title) or d.url
        result = await asyncio.to_thread(download_document, d.url)
        if result["download_status"] == "downloaded":
            documents.append(
                DeliveryDocument(
                    title=title,
                    url=d.url,
                    document_type="other",
                    mime_type=result.get("mime_type"),
                    download_status="downloaded",
                    storage_uri=result["storage_uri"],
                    sha256=result["sha256"],
                )
            )
        else:
            documents.append(
                DeliveryDocument(
                    title=title,
                    url=d.url,
                    document_type="other",
                    download_status="failed",
                )
            )
    return documents


async def _build_delivery_record(
    raw: ExtractedOpportunity,
    *,
    page_url: str,
    page_content: str,
    state_code: str,
    portal_name: str,
    base_url: str,
    run_id: str,
) -> DeliveryRecord:
    errors: List[str] = [
        "extracted via Gemini dynamic extraction (no selectors); verify "
        "field accuracy before treating as authoritative"
    ]

    title = clean_text(raw.title)
    if not title:
        errors.append("opportunity.title not found on page")

    submission_url = raw.submission_url or page_url
    if not raw.submission_url:
        errors.append("submission_url not found; falling back to listing page URL")

    # Reformats only components actually present in the raw string - see
    # src/normalizer.py; never fabricates a time-of-day or timezone offset
    # that the source didn't provide (scraper-team-minimum-spec.md).
    published_at = parse_date_with_fallback(raw.published_at_raw)
    if raw.published_at_raw and not published_at:
        errors.append(f"could not parse published_at from {raw.published_at_raw!r}")

    due_at = parse_date_with_fallback(raw.due_at_raw)
    if raw.due_at_raw and not due_at:
        errors.append(f"could not parse due_at from {raw.due_at_raw!r}")

    documents = await _download_documents(raw.documents)
    for d in documents:
        if d.download_status == "failed":
            errors.append(f"document download failed: {d.url}")

    # Save the raw page content to the approved object-storage location
    # (scraper-team-minimum-spec.md: raw_capture is "required for a
    # successful scrape" and must include a storage URI, not just a hash).
    raw_storage_uri, raw_sha256 = await asyncio.to_thread(
        save_text, page_content, prefix="raw_captures"
    )

    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_opportunity_id = clean_text(raw.solicitation_number) or title or page_url

    return DeliveryRecord(
        observed_at=now_str,
        source=DeliverySource(
            name=portal_name,
            base_url=base_url,
            opportunity_url=submission_url,
            source_opportunity_id=source_opportunity_id,
            access_level="public",
        ),
        opportunity=DeliveryOpportunity(
            title=title,
            agency_name=clean_text(raw.agency_name),
            solicitation_number=clean_text(raw.solicitation_number),
            status=clean_text(raw.status),
            notice_type=clean_text(raw.notice_type),
            published_at=published_at,
            due_at=due_at,
            source_timezone=clean_text(raw.source_timezone),
            description=clean_text(raw.description),
            categories=[c for c in (clean_text(c) for c in raw.categories) if c],
            submission_url=submission_url,
            submission_method="external_portal",
        ),
        documents=documents,
        raw_capture=RawCapture(
            content_type="text/plain",
            storage_uri=raw_storage_uri,
            sha256=raw_sha256,
        ),
        crawl=CrawlInfo(
            run_id=run_id,
            parser_version=PARSER_VERSION,
            # "ok" | "partial" | "error" per scraper-team-minimum-spec.md;
            # errors[0] is always the standing "verify before treating as
            # authoritative" notice, so more than one entry means a real
            # field-level problem was found.
            record_status="ok" if len(errors) == 1 else "partial",
            errors=errors,
        ),
    )


async def _fetch_page_content(crawler: AsyncWebCrawler, url: str) -> str:
    result = await fetch_page(crawler, url)
    return result.markdown or result.cleaned_html or result.html or ""


async def scrape_dynamic(
    crawler: AsyncWebCrawler,
    list_url: str,
    *,
    state_code: str,
    portal_name: str,
    base_url: str,
    run_id: str,
    follow_detail_pages: bool = True,
) -> List[DeliveryRecord]:
    """Fetch `list_url` through Crawl4AI and extract every opportunity on it
    via Gemini, with no portal-specific selector code. Intended for
    onboarding a brand-new/unknown portal quickly; see module docstring for
    when NOT to use this over a selector-based scraper.

    When a listing links to its own detail page (`detail_page_url`) and
    `follow_detail_pages` is true (the default), that page is fetched and
    extracted too, then merged over the listing-page data - many portals
    only show documents/full descriptions on the detail page, not the
    listing. A detail-page fetch/extraction failure just falls back to
    listing-page-only data for that one opportunity; it never drops the
    opportunity or fails the whole run.

    Raises RobotsDisallowedError if `list_url`'s robots.txt disallows
    fetching it. A disallowed detail_page_url is not fatal - that one
    opportunity just falls back to listing-page-only data, same as any
    other detail-page fetch failure.
    """
    if not await robots_allow(crawler, list_url):
        raise RobotsDisallowedError(f"robots.txt disallows fetching {list_url}")

    list_content = await _fetch_page_content(crawler, list_url)
    if not list_content.strip():
        return []

    raw_opportunities = extract_opportunities_from_page(list_content, page_url=list_url)

    records: List[DeliveryRecord] = []
    for raw in raw_opportunities:
        record_page_url = list_url
        record_content = list_content

        if follow_detail_pages and raw.detail_page_url and await robots_allow(
            crawler, raw.detail_page_url
        ):
            try:
                detail_content = await _fetch_page_content(crawler, raw.detail_page_url)
                if detail_content.strip():
                    detail_raws = extract_opportunities_from_page(
                        detail_content, page_url=raw.detail_page_url
                    )
                    if detail_raws:
                        raw = _merge_opportunity(raw, detail_raws[0])
                        record_page_url = raw.detail_page_url
                        record_content = detail_content
            except Exception:
                pass  # keep the listing-page-only data for this opportunity

        records.append(
            await _build_delivery_record(
                raw,
                page_url=record_page_url,
                page_content=record_content,
                state_code=state_code,
                portal_name=portal_name,
                base_url=base_url,
                run_id=run_id,
            )
        )

    return records
