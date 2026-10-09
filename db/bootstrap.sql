-- One-time setup: creates a dedicated role and database for this scraper
-- project, isolated from the postgres superuser. Run once as an admin:
--
--   sudo -u postgres psql -f db/bootstrap.sql
--
-- Change the password below before running, then set DATABASE_URL to match
-- (see .env.example).

CREATE ROLE procurement_scraper WITH LOGIN PASSWORD 'changeme';
CREATE DATABASE procurement_scraper_db OWNER procurement_scraper;
