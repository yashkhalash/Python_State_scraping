-- Schema for the tables this scraper project actually writes to.
--
-- Scope: only the "Source collection and retention" section of
-- postgresql-data-dictionary.md (retention_policy, source_system,
-- source_endpoint, crawl_run, source_record, source_record_version,
-- raw_artifact). Everything past that section - opportunity dedup,
-- taxonomy/classification, business matching, awards, embeddings - is
-- downstream processing owned by other teams per
-- scraper-team-minimum-spec.md ("This is a data-collection assignment
-- only. Other teams handle all processing after delivery.") and is
-- intentionally NOT created here.
--
-- Run after db/bootstrap.sql, connected as the procurement_scraper role:
--   psql "$DATABASE_URL" -f db/schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS procurement;

CREATE TABLE IF NOT EXISTS procurement.retention_policy (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name               text NOT NULL,
    retention_months   integer NOT NULL DEFAULT 3,
    scope              text NOT NULL,
    purge_mode         text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS procurement.source_system (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                      text NOT NULL,
    base_url                  text NOT NULL,
    source_type               text NOT NULL,
    access_method             text NOT NULL,
    terms_url                 text,
    robots_policy_url         text,
    default_timezone          text,
    active                    boolean NOT NULL DEFAULT true,
    last_successful_crawl_at  timestamptz,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, base_url)
);

CREATE TABLE IF NOT EXISTS procurement.source_endpoint (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id   uuid NOT NULL REFERENCES procurement.source_system(id),
    endpoint_url        text NOT NULL,
    endpoint_kind      text NOT NULL,
    crawl_interval     interval,
    active             boolean NOT NULL DEFAULT true,
    UNIQUE (source_system_id, endpoint_url)
);

CREATE TABLE IF NOT EXISTS procurement.crawl_run (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_endpoint_id    uuid NOT NULL REFERENCES procurement.source_endpoint(id),
    scraper_version       text NOT NULL,
    started_at            timestamptz NOT NULL DEFAULT now(),
    completed_at          timestamptz,
    status                text NOT NULL DEFAULT 'running',
    request_count         integer NOT NULL DEFAULT 0,
    records_found         integer NOT NULL DEFAULT 0,
    error_summary         text,
    metrics               jsonb
);

CREATE TABLE IF NOT EXISTS procurement.source_record (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id      uuid NOT NULL REFERENCES procurement.source_system(id),
    external_id           text,
    fallback_fingerprint  text,
    record_type           text NOT NULL DEFAULT 'opportunity',
    canonical_url         text NOT NULL,
    access_level          text NOT NULL DEFAULT 'public',
    first_seen_at         timestamptz NOT NULL DEFAULT now(),
    last_seen_at          timestamptz NOT NULL DEFAULT now(),
    retained_until        timestamptz NOT NULL,
    purge_status          text NOT NULL DEFAULT 'active',
    purged_at             timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (external_id IS NOT NULL OR fallback_fingerprint IS NOT NULL)
);

-- One row per (source_system, external_id) - the same opportunity
-- observed again updates last_seen_at rather than creating a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS source_record_external_id_uq
    ON procurement.source_record (source_system_id, external_id)
    WHERE external_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS source_record_fingerprint_uq
    ON procurement.source_record (source_system_id, fallback_fingerprint)
    WHERE external_id IS NULL AND fallback_fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS procurement.source_record_version (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_record_id       uuid NOT NULL REFERENCES procurement.source_record(id),
    crawl_run_id           uuid NOT NULL REFERENCES procurement.crawl_run(id),
    observed_at            timestamptz NOT NULL,
    content_hash           text NOT NULL,
    raw_payload_uri        text,
    candidate_fields       jsonb NOT NULL,
    parser_version         text NOT NULL,
    extraction_confidence  numeric(4,3),
    retained_until         timestamptz NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    -- source_record_version rows are immutable observed snapshots; the
    -- same exact content is never stored twice for one source_record.
    UNIQUE (source_record_id, content_hash)
);

CREATE TABLE IF NOT EXISTS procurement.raw_artifact (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_record_version_id uuid NOT NULL REFERENCES procurement.source_record_version(id),
    artifact_kind            text NOT NULL,
    storage_uri              text,
    source_url               text NOT NULL,
    mime_type                text,
    byte_size                bigint,
    checksum_sha256          text NOT NULL,
    retrieved_at             timestamptz NOT NULL,
    retained_until           timestamptz NOT NULL,
    purge_status             text NOT NULL DEFAULT 'active',
    purged_at                timestamptz
);

CREATE INDEX IF NOT EXISTS source_record_version_record_idx
    ON procurement.source_record_version (source_record_id);
CREATE INDEX IF NOT EXISTS raw_artifact_version_idx
    ON procurement.raw_artifact (source_record_version_id);
CREATE INDEX IF NOT EXISTS crawl_run_endpoint_idx
    ON procurement.crawl_run (source_endpoint_id);
