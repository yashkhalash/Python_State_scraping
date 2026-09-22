# External Procurement Scraper: Minimum Delivery Specification

## Assignment description

Collect publicly accessible procurement notices from assigned state and local-government websites. For each notice, collect the page, its available metadata, and linked public procurement documents. Deliver the collected data in the JSONL format below.

This is a data-collection assignment only. Other teams handle all processing after delivery. Do not infer business meaning, alter source values, or make decisions about whether two notices describe the same procurement.

## Scope and confidentiality

Work only on the websites, endpoints, and storage locations assigned to you. Do not seek or use information beyond what is explicitly needed to complete this collection assignment. Do not share collected data, access credentials, storage locations, code, or project materials with anyone outside the authorized project contacts.

## Rules

- Use only public pages, public feeds, public APIs, or accounts explicitly authorized for this work.
- Do not bypass logins, payment requirements, CAPTCHAs, robots restrictions, rate limits, or other access controls.
- Preserve the source value. Do not guess missing dates, categories, agencies, or status.
- Submit one record per observed opportunity. If the source content changes, submit a new record; do not overwrite an earlier delivered record.
- Download linked PDFs/documents only when publicly accessible and permitted. Include every document URL even when a download fails.
- All timestamps must be ISO 8601. Supply the source timezone if known; otherwise use `null`.

## Required output

Deliver newline-delimited JSON (`.jsonl`): one JSON object per opportunity per crawl. UTF-8 only.

```json
{
  "schema_version": "1.0",
  "observed_at": "2026-09-19T14:32:01Z",
  "source": {
    "name": "Example City Procurement Portal",
    "base_url": "https://bids.example.gov",
    "opportunity_url": "https://bids.example.gov/solicitations/12345",
    "source_opportunity_id": "12345",
    "access_level": "public"
  },
  "opportunity": {
    "title": "Road resurfacing services",
    "agency_name": "Example City Public Works",
    "solicitation_number": "PW-2026-17",
    "status": "open",
    "notice_type": "IFB",
    "published_at": "2026-09-15T09:00:00-04:00",
    "due_at": "2026-10-15T14:00:00-04:00",
    "source_timezone": "America/New_York",
    "description": "Short source-provided description, if available.",
    "categories": ["Construction", "Roadwork"],
    "submission_url": "https://bids.example.gov/solicitations/12345",
    "submission_method": "external_portal"
  },
  "documents": [
    {
      "title": "IFB document",
      "url": "https://bids.example.gov/files/12345.pdf",
      "document_type": "solicitation",
      "mime_type": "application/pdf",
      "download_status": "downloaded",
      "storage_uri": "s3://approved-bucket/source/example-city/12345/ifb.pdf",
      "sha256": "hex-encoded SHA-256 hash"
    }
  ],
  "raw_capture": {
    "content_type": "text/html",
    "storage_uri": "s3://approved-bucket/source/example-city/12345/page.html",
    "sha256": "hex-encoded SHA-256 hash"
  },
  "crawl": {
    "run_id": "team-run-2026-09-19-001",
    "parser_version": "example-city-v1.2.0",
    "record_status": "ok",
    "errors": []
  }
}
```

## Field requirements

| Field | Requirement |
|---|---|
| `observed_at` | Required. UTC timestamp for when the page was collected. |
| `source.name`, `source.base_url`, `source.opportunity_url` | Required. |
| `source.source_opportunity_id` | Required when the source provides an ID; otherwise `null`. |
| `opportunity.title` | Required when shown by the source; otherwise `null` and add an error. |
| `agency_name`, `solicitation_number`, `status`, `notice_type` | Collect if present; otherwise `null`. Preserve source wording for `status` and `notice_type`. |
| `published_at`, `due_at`, `source_timezone` | Collect exactly as shown. A date without a time may be sent as a date string (`YYYY-MM-DD`). Never invent a time or timezone. |
| `description` | Source summary only. Do not summarize, rewrite, or add content. |
| `categories` | Array of the portal's original category labels, for example `Agriculture`, `Technology`, or `Logistics`. Empty array when none are present. |
| `documents` | Array; include all linked solicitation PDFs, addenda, Q&A, pricing sheets, plans, and award notices when visible. |
| `raw_capture` | Required for a successful scrape. Save the HTML/JSON/API response and provide its approved storage URI plus SHA-256. |
| `crawl` | Required. Include parser version and any missing/download/error conditions. |

## Allowed values

Use these values when known; otherwise use `null` rather than inventing a value.

```text
access_level:      public | registration_required | paid | unknown
record_status:     ok | partial | error
download_status:   downloaded | link_only | failed | not_applicable
submission_method: external_portal | email | physical | hybrid | unknown
```

`status`, `notice_type`, and `document_type` may use source-provided text. For common document types, prefer: `solicitation`, `addendum`, `qa`, `pricing_sheet`, `plans`, `terms`, `award_notice`, or `other`.

## PDF and document handling

1. Collect every visible document link.
2. If download is permitted, download the file unchanged; do not OCR, summarize, or edit it.
3. Calculate SHA-256 for every downloaded file.
4. Save the file and raw page response to the approved object-storage location.
5. If a document cannot be downloaded, set `download_status` to `link_only` or `failed` and add a short error message. The document URL must still be included.

## Change and error handling

- Use the source opportunity ID when available. If none exists, use the exact opportunity URL.
- Emit a record whenever the listing is observed. Include the raw-page and document hashes exactly as described above.
- For an updated deadline, addendum, cancellation, or award, emit the latest source record and include all currently visible documents.
- If a detail page fails but the listing page is available, submit a `partial` record with the listing information and a precise error.
- Do not delete or replace previously delivered records.

## Three-month retention

All source content has a three-calendar-month retention period beginning at `observed_at`.

- Store each captured page, JSON response, and downloaded document with its capture time and SHA-256.
- Keep the source content available to the receiving team for three months.
- Do not retain copies beyond that period unless the project owner gives written direction.
- The receiving team performs the final removal process.

## Delivery checklist

Before delivering a crawl result, verify:

- Each record has a source URL and `observed_at`.
- Date/time values were copied exactly; missing values are `null`.
- Source categories are preserved as strings.
- All visible PDF/document URLs are included.
- Downloaded files and raw captures have a SHA-256 and approved storage URI.
- Failures are in `crawl.errors`; no fields were fabricated to hide them.
