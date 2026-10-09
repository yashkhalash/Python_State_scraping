#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

STATES=(GA AL MT UT IA DE)
DATE_TAG=$(date +%F)

mkdir -p logs data/output

for STATE in "${STATES[@]}"; do
    ext="jsonl"
    [[ "$STATE" == "GA" ]] && ext="json"
    uv run python run_scraper.py \
        --state "$STATE" \
        --output "data/output/${STATE,,}_${DATE_TAG}.${ext}" \
        >> "logs/${STATE,,}.log" 2>&1
done

uv run python purge_expired.py >> logs/purge.log 2>&1
