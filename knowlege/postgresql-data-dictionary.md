# PostgreSQL Data Dictionary

This document describes every table and column in [the PostgreSQL physical schema](postgresql-physical-schema.md). The physical-schema document remains the source for exact data types, constraints, and indexes.

## Common conventions

- `id` is the UUID primary key for a row unless the table declares a composite primary key.
- `*_id` is a UUID foreign key to the named entity.
- `created_at` records when this platform created the row; `updated_at` records its last local update.
- `observed_at` and `retrieved_at` are when source content was collected. `published_at`, `due_at`, and `occurred_at` are source lifecycle times.
- `retained_until` is the time the acquired source data must be deleted. It is three calendar months after observation.
- `confidence`, `completeness`, and `score` use `0.000` through `1.000`.
- `*_raw` retains source wording. `jsonb` columns retain variable, source-specific structures.

## Source collection and retention

### `procurement.retention_policy`

Controls deletion rules for acquired procurement content.

- `id` — unique policy identifier.
- `name` — human-readable policy name.
- `retention_months` — required retention duration; fixed at three months.
- `scope` — category of data governed by the policy.
- `purge_mode` — deletion behavior after expiry.
- `created_at` — when the policy was created.

### `procurement.source_system`

Represents one procurement portal, public API, feed, purchasing group, or aggregator.

- `id` — unique source identifier.
- `name` — source display name.
- `base_url` — root URL for the source.
- `source_type` — kind of source, such as portal or API.
- `access_method` — permitted method used to collect from the source.
- `terms_url` — link to source terms, if available.
- `robots_policy_url` — link to robots/access guidance, if available.
- `default_timezone` — source's default IANA timezone.
- `active` — whether collection is currently enabled.
- `last_successful_crawl_at` — latest successful collection time.
- `created_at` — when the source was registered.
- `updated_at` — most recent local source-configuration update.

### `procurement.source_endpoint`

Represents a collectable source URL or endpoint.

- `id` — unique endpoint identifier.
- `source_system_id` — source that owns the endpoint.
- `endpoint_url` — URL or API endpoint to collect.
- `endpoint_kind` — listing, detail, document, API, or feed endpoint.
- `crawl_interval` — intended time between collection runs.
- `active` — whether this endpoint is enabled.

### `procurement.crawl_run`

Records one scraper execution against an endpoint.

- `id` — unique crawl-run identifier.
- `source_endpoint_id` — endpoint collected during the run.
- `scraper_version` — version of scraper/parser used.
- `started_at` — crawl start time.
- `completed_at` — crawl completion time, if finished.
- `status` — run outcome.
- `request_count` — number of source requests made.
- `records_found` — number of source records found.
- `error_summary` — short failure/partial-run explanation.
- `metrics` — optional run metrics that do not merit dedicated columns.

### `procurement.source_record`

Stores the durable identity of one item as a particular source identifies it.

- `id` — unique source-record identifier.
- `source_system_id` — source that supplied the item.
- `external_id` — source-provided opportunity, notice, award, or document ID.
- `fallback_fingerprint` — deterministic identity key when no source ID exists.
- `record_type` — type of source item.
- `canonical_url` — preferred source URL for the item.
- `access_level` — access condition observed at the source.
- `first_seen_at` — first time the item was collected.
- `last_seen_at` — most recent time the item was collected.
- `retained_until` — expiry time for this acquired item.
- `purge_status` — current deletion workflow state.
- `purged_at` — time deletion completed, if applicable.
- `created_at` — local row creation time.

### `procurement.source_record_version`

Stores an immutable observed version of a source record.

- `id` — unique version identifier.
- `source_record_id` — stable source record being observed.
- `crawl_run_id` — crawl that produced the version.
- `observed_at` — source observation time.
- `content_hash` — hash used to identify identical captured content.
- `raw_payload_uri` — object-storage location of raw source content.
- `candidate_fields` — extracted source fields before normalization.
- `parser_version` — extractor version that produced the candidate fields.
- `extraction_confidence` — overall confidence in the extraction.
- `retained_until` — expiry time for the version.
- `created_at` — local row creation time.

### `procurement.raw_artifact`

Tracks an immutable raw HTML, JSON, XML, PDF, or file capture.

- `id` — unique artifact identifier.
- `source_record_version_id` — source version represented by the artifact.
- `artifact_kind` — content format/category.
- `storage_uri` — approved object-storage location.
- `source_url` — exact URL used to obtain the artifact.
- `mime_type` — declared content type.
- `byte_size` — artifact size in bytes.
- `checksum_sha256` — SHA-256 checksum of the stored artifact.
- `retrieved_at` — retrieval time.
- `retained_until` — expiry time for the artifact.
- `purge_status` — current deletion workflow state.
- `purged_at` — time deletion completed, if applicable.

## Government entities and taxonomies

### `procurement.jurisdiction`

Represents a government geography or government-like area.

- `id` — unique jurisdiction identifier.
- `parent_jurisdiction_id` — enclosing jurisdiction, when one exists.
- `jurisdiction_type` — state, county, municipality, district, tribal entity, authority, or other.
- `name` — jurisdiction name.
- `state_code` — two-character U.S. state code when applicable.
- `fips_code` — applicable FIPS identifier.
- `geo` — optional geographic geometry.

### `procurement.buying_organization`

Represents an agency, department, school district, utility, authority, or procurement office.

- `id` — unique organization identifier.
- `parent_organization_id` — parent agency or organization, if any.
- `primary_jurisdiction_id` — principal jurisdiction of the buyer.
- `legal_name` — official legal name.
- `display_name` — source-facing or shortened name.
- `organization_type` — government, school district, utility, authority, or other.
- `website_url` — organization website.
- `active` — whether the organization is currently active.
- `created_at` — local row creation time.
- `updated_at` — most recent local update time.

### `procurement.organization_alias`

Maps a source-specific buyer name or ID to a canonical buying organization.

- `id` — unique alias identifier.
- `buying_organization_id` — canonical organization represented by the alias.
- `source_system_id` — source in which the alias occurs.
- `external_id` — source-specific buyer ID, if available.
- `name_raw` — buyer name exactly as supplied by the source.
- `confidence` — confidence in the alias mapping.

### `procurement.taxonomy`

Defines a classification system, such as NIGP, NAICS, UNSPSC, or an internal category hierarchy.

- `id` — unique taxonomy identifier.
- `code` — stable taxonomy code.
- `name` — taxonomy display name.
- `version` — publisher/version reference, if any.
- `is_standard` — whether this is a recognized external standard.

### `procurement.taxonomy_term`

Represents one term or code in a taxonomy.

- `id` — unique term identifier.
- `taxonomy_id` — taxonomy containing this term.
- `parent_term_id` — broader parent term, if any.
- `code` — code within the taxonomy.
- `label` — display label.
- `normalized_label` — normalized label used for matching and deduplication.
- `path` — optional hierarchy path.

## Opportunity core and lifecycle

### `procurement.opportunity`

Represents the canonical procurement opportunity after source records are linked.

- `id` — unique opportunity identifier.
- `canonical_title` — normalized display title.
- `opportunity_type` — solicitation type, such as RFP, IFB, RFQ, RFI, grant, or auction.
- `status` — current lifecycle status.
- `buying_organization_id` — buyer issuing the opportunity.
- `primary_jurisdiction_id` — principal government geography.
- `published_at` — source publication time.
- `due_at` — submission deadline.
- `source_timezone` — IANA timezone used by the source for local times.
- `estimated_value_amount` — estimated monetary value, if provided.
- `currency` — ISO currency for the estimated value.
- `canonical_url` — preferred official or most-authoritative source URL.
- `first_seen_at` — first local observation time.
- `last_seen_at` — latest local observation time.
- `closed_at` — closure, cancellation, or award completion time.
- `dedupe_confidence` — confidence that linked records describe one opportunity.
- `retained_until` — expiry time for acquired opportunity content.
- `created_at` — local row creation time.
- `updated_at` — most recent local update time.

### `procurement.opportunity_source_record`

Joins an opportunity to each source record that describes it.

- `opportunity_id` — canonical opportunity.
- `source_record_id` — source-specific record.
- `relationship_type` — primary, direct member, syndicated, mirrored, or aggregated relationship.
- `is_authoritative` — whether the source is considered governing for this relationship.
- `confidence` — confidence in the source-to-opportunity mapping.

### `procurement.opportunity_snapshot`

Stores the latest normalized projection for fast retrieval; it can be rebuilt from source versions.

- `opportunity_id` — opportunity represented by this snapshot.
- `generated_at` — time the snapshot was generated.
- `normalization_version` — normalizer/mapping version used.
- `title_tsv` — full-text search representation of the title.
- `normalized_attributes` — normalized values without dedicated columns.
- `completeness` — share of expected fields populated.
- `confidence` — aggregate confidence in the snapshot.

### `procurement.opportunity_event`

Represents a dated lifecycle event such as publication, addendum, deadline change, cancellation, closure, or award notice.

- `id` — unique event identifier.
- `opportunity_id` — opportunity affected by the event.
- `source_record_version_id` — observed source version supporting the event.
- `event_type` — lifecycle event category.
- `occurred_at` — event time.
- `source_timezone` — source timezone for the event.
- `summary` — source-derived short event description.
- `details` — variable event data.
- `retained_until` — expiry time for acquired event content.

### `procurement.opportunity_classification`

Assigns a taxonomy term to an opportunity.

- `opportunity_id` — classified opportunity.
- `taxonomy_term_id` — assigned classification term.
- `source_record_version_id` — source version supporting the assignment.
- `assignment_source` — portal, rules, AI, or human assignment method.
- `confidence` — confidence in the assignment.
- `retained_until` — expiry time for acquired classification data.

### `procurement.opportunity_requirement`

Captures a specific procurement requirement, including its source wording and structured form.

- `id` — unique requirement identifier.
- `opportunity_id` — opportunity containing the requirement.
- `requirement_type` — requirement category, such as bond, insurance, license, certification, or experience.
- `value_json` — structured requirement value.
- `original_text` — source wording.
- `source_record_version_id` — source version supporting the requirement.
- `confidence` — confidence in the extraction.
- `retained_until` — expiry time for acquired requirement data.

## Distribution and solicitation details

### `procurement.procurement_network`

Represents a state/regional purchasing group, cooperative, or comparable distribution network.

- `id` — unique network identifier.
- `source_system_id` — associated portal/source, if any.
- `name` — network name.
- `network_type` — state group, regional group, cooperative, portal, or other.
- `geographic_coverage` — structured description of covered areas.
- `active` — whether the network is currently active.

### `procurement.network_membership`

Records a buyer's membership in a procurement network.

- `procurement_network_id` — network.
- `buying_organization_id` — participating buyer.
- `member_from` — membership start date.
- `member_to` — membership end date, if known.
- `status` — membership status.

### `procurement.opportunity_distribution`

Records how an opportunity is distributed through a source or procurement network.

- `id` — unique distribution identifier.
- `opportunity_id` — distributed opportunity.
- `procurement_network_id` — network carrying it, if any.
- `source_record_id` — source record representing that distribution.
- `distribution_type` — direct member, aggregated, syndicated, or mirrored.
- `first_published_at` — first publication time in that distribution.
- `access_level` — observed access condition.
- `retained_until` — expiry time for acquired distribution data.

### `procurement.opportunity_lot`

Represents a lot, section, or separately awarded component of an opportunity.

- `id` — unique lot identifier.
- `opportunity_id` — parent opportunity.
- `lot_number` — source lot/section number.
- `title` — lot title.
- `description` — lot description.
- `estimated_value_amount` — estimated monetary value for the lot.
- `currency` — ISO currency for the lot value.
- `starts_at` — lot period start, if stated.
- `ends_at` — lot period end, if stated.
- `status` — source lot status.
- `retained_until` — expiry time for acquired lot data.

### `procurement.opportunity_line_item`

Represents a priced or requested item within an opportunity or lot.

- `id` — unique line-item identifier.
- `opportunity_id` — parent opportunity.
- `lot_id` — containing lot, if any.
- `line_number` — source line number.
- `description` — source item description.
- `quantity` — requested quantity.
- `unit_of_measure` — unit for the quantity.
- `pricing_model` — fixed price, unit price, hourly, other, or unspecified.
- `retained_until` — expiry time for acquired line-item data.

### `procurement.opportunity_location`

Represents a performance, delivery, meeting, or service-area location.

- `id` — unique location identifier.
- `opportunity_id` — related opportunity.
- `location_type` — purpose of the location.
- `address_line_1` — first street-address line.
- `address_line_2` — second street-address line.
- `city` — city/locality.
- `state_code` — two-character state code.
- `postal_code` — postal/ZIP code.
- `country_code` — two-character country code.
- `geo` — optional geographic point.
- `raw_location_text` — unparsed source location text.
- `retained_until` — expiry time for acquired location data.

### `procurement.opportunity_contact`

Represents a contact listed for procurement, technical, or submission questions.

- `id` — unique contact identifier.
- `opportunity_id` — related opportunity.
- `contact_role` — procurement, technical, submission, or other contact role.
- `name` — contact name.
- `email` — contact email address.
- `phone` — contact phone number.
- `source_record_version_id` — source version that listed the contact.
- `retained_until` — expiry time for acquired contact data.

### `procurement.submission_requirement`

Captures the official means and requirements for submitting a response; it does not store a submitted bid.

- `opportunity_id` — opportunity with the submission requirement.
- `method` — external portal, email, physical, hybrid, or unknown.
- `submission_url` — official submission or instruction URL.
- `requires_registration` — whether source registration is required.
- `requires_intent_to_bid` — whether intent-to-bid is required.
- `allows_electronic_submission` — whether electronic submission is allowed.
- `requires_pricing_form` — whether a pricing form is required.
- `requires_questionnaire` — whether a structured questionnaire is required.
- `instructions_text` — source submission instructions.
- `source_record_version_id` — source version supporting the requirement.
- `retained_until` — expiry time for acquired submission data.

### `procurement.opportunity_question`

Stores a formal source question-and-answer entry.

- `id` — unique question identifier.
- `opportunity_id` — related opportunity.
- `question_text` — published question text.
- `answer_text` — published answer text.
- `published_at` — publication time.
- `source_record_version_id` — source version supporting the Q&A.
- `retained_until` — expiry time for acquired Q&A content.

## Documents and semantic retrieval

### `procurement.document`

Represents a logical document associated with an opportunity.

- `id` — unique document identifier.
- `opportunity_id` — related opportunity.
- `replaces_document_id` — prior document superseded by this document, if any.
- `document_role` — solicitation, addendum, Q&A, pricing sheet, plans, terms, award notice, or other.
- `officiality` — authority level of the document.
- `title` — document title.
- `published_at` — source publication time.
- `retrieved_at` — collection time.
- `retained_until` — expiry time for acquired document data.

### `procurement.document_version`

Represents an immutable file version of a logical document.

- `id` — unique document-version identifier.
- `document_id` — parent logical document.
- `source_record_version_id` — source version that supplied the file.
- `version_number` — sequential version number for the document.
- `storage_uri` — approved object-storage location.
- `content_hash` — hash of the file content.
- `mime_type` — document content type.
- `byte_size` — file size in bytes.
- `retained_until` — expiry time for acquired file data.

### `procurement.document_text`

Stores extracted text for one document version.

- `document_version_id` — document version whose text was extracted.
- `text_content` — extracted full text.
- `language_code` — detected or declared language.
- `ocr_used` — whether OCR was needed.
- `extraction_version` — text-extraction software/version.
- `search_tsv` — full-text search representation.
- `retained_until` — expiry time for extracted text.

### `procurement.document_chunk`

Stores a page/range-addressable segment of a document for retrieval and citations.

- `id` — unique chunk identifier.
- `document_version_id` — document version containing the chunk.
- `chunk_index` — chunk sequence number.
- `chunk_text` — text in the chunk.
- `heading_path` — headings above the chunk, if detected.
- `page_start` — first document page covered.
- `page_end` — last document page covered.
- `character_start` — first character offset in extracted document text.
- `character_end` — ending character offset in extracted document text.
- `retained_until` — expiry time for chunk text.

### `procurement.embedding_model`

Defines an embedding model usable for document chunks.

- `id` — unique embedding-model identifier.
- `provider` — model provider name.
- `model_name` — provider model name.
- `dimensions` — required number of vector dimensions.
- `active` — whether the model is available for new embeddings.

### `procurement.embedding`

Stores one semantic vector for one document chunk and model.

- `id` — unique embedding identifier.
- `document_chunk_id` — chunk represented by the vector.
- `embedding_model_id` — model that generated the vector.
- `embedding` — pgvector numeric representation.
- `retained_until` — expiry time for the vector.

### `procurement.opportunity_tag`

Assigns a category/tag to an opportunity while preserving how the tag was obtained.

- `opportunity_id` — tagged opportunity.
- `taxonomy_term_id` — category term, such as Agriculture, Technology, or Logistics.
- `source_record_version_id` — supporting source version, if any.
- `tag_source` — portal, rules, AI, or human source of the tag.
- `confidence` — confidence in the tag assignment.
- `retained_until` — expiry time for acquired tag data.

### `procurement.extracted_fact`

Stores one structured fact extracted from procurement content.

- `id` — unique fact identifier.
- `opportunity_id` — opportunity the fact concerns.
- `fact_type` — type of fact, such as bonding amount or insurance requirement.
- `value_json` — structured fact value.
- `confidence` — confidence in the extracted fact.
- `extraction_model_version` — extraction process/model version.
- `retained_until` — expiry time for the fact.

### `procurement.fact_evidence`

Links an extracted fact to the document chunk supporting it.

- `extracted_fact_id` — fact supported by the evidence.
- `document_chunk_id` — source document chunk.
- `page_start` — first cited page.
- `page_end` — last cited page.
- `quoted_locator` — additional citation locator, such as heading or character range.

## Business profiles and private recommendations

### `business.business`

Represents one platform-managed business profile.

- `id` — unique business identifier.
- `legal_name` — legal business name.
- `public_name` — optional display name.
- `website_url` — business website.
- `profile_visibility` — whether data can participate in private partner matching.
- `created_at` — local row creation time.
- `updated_at` — most recent local update time.

### `business.business_capability`

Stores a business-provided capability.

- `id` — unique capability identifier.
- `business_id` — business that owns the capability.
- `capability_text` — capability description.
- `taxonomy_term_id` — optional standardized category.
- `evidence_url` — optional supporting webpage/document URL.
- `visibility` — visibility allowed for matching.
- `created_at` — local row creation time.

### `business.business_certification`

Stores a business-provided certification or designation.

- `id` — unique certification identifier.
- `business_id` — business that owns the certification.
- `certification_type` — certification category/name.
- `issuer` — issuing organization.
- `identifier` — certificate number or reference, if stored.
- `valid_from` — validity start date.
- `valid_until` — validity end date.
- `visibility` — visibility allowed for matching.

### `business.opportunity_match`

Stores a generated relevance result between a business and an opportunity.

- `business_id` — business evaluated.
- `opportunity_id` — opportunity evaluated.
- `score` — match score.
- `match_version` — matching algorithm/rules version.
- `reasons` — machine-readable explanations for the score.
- `generated_at` — time the match was generated.
- `notified_at` — time a notification was sent, if any.
- `dismissed_at` — time the business dismissed the match, if any.
- `saved_at` — time the business saved the match, if any.
- `retained_until` — expiry time tied to the opportunity content.

### `business.partner_recommendation`

Stores a private recommendation of one business to another, optionally for a specific opportunity.

- `id` — unique recommendation identifier.
- `requesting_business_id` — business receiving the recommendation.
- `candidate_business_id` — potential partner business.
- `opportunity_id` — related opportunity, if the recommendation is opportunity-specific.
- `score` — recommendation score.
- `reasons` — machine-readable explanations for the recommendation.
- `status` — active, saved, or dismissed state.
- `generated_at` — time the recommendation was generated.

## Awards

### `procurement.award`

Represents an award or a terminal no-award/cancelled outcome for an opportunity.

- `id` — unique award identifier.
- `opportunity_id` — opportunity associated with the outcome.
- `award_status` — awarded, no award, cancelled, or award without solicitation.
- `award_date` — award/outcome date.
- `total_award_amount` — total monetary award amount.
- `currency` — ISO currency for the award amount.
- `term` — contract term as a timestamp range, if known.
- `retained_until` — expiry time for acquired award data.

### `procurement.award_recipient`

Represents an awardee named by a source.

- `id` — unique award-recipient identifier.
- `award_id` — award containing this recipient.
- `award_lot_id` — awarded lot, if applicable.
- `business_name_raw` — awardee name exactly as published.
- `matched_business_id` — optional match to a platform business profile.
- `award_amount` — recipient/lot award amount.
- `retained_until` — expiry time for acquired award-recipient data.

