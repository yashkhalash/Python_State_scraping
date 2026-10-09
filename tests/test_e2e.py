import json
import pytest
from pathlib import Path
from run_scraper import run_scraper_pipeline, main

@pytest.mark.asyncio
async def test_run_scraper_pipeline_ga(tmp_path):
    output_file = tmp_path / "georgia_bids.json"
    count = await run_scraper_pipeline(state="GA", output_path=output_file, max_pages=1, headless=True)
    assert isinstance(count, int)
    assert count >= 0
    assert output_file.exists()
    
    with open(output_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) == count

@pytest.mark.asyncio
async def test_run_scraper_pipeline_alabama(tmp_path):
    output_file = tmp_path / "alabama_bids.jsonl"
    count = await run_scraper_pipeline(state="AL", output_path=output_file, max_pages=1, headless=True)
    assert isinstance(count, int)
    assert count >= 0
    assert output_file.exists()

    # scraper-team-minimum-spec.md's "Required output": newline-delimited
    # JSON, one JSON object per opportunity per crawl - not a JSON array.
    with open(output_file, "r", encoding="utf-8") as f:
        lines = [line for line in f.read().splitlines() if line.strip()]
    data = [json.loads(line) for line in lines]
    assert len(data) == count

@pytest.mark.asyncio
async def test_run_scraper_pipeline_invalid_state(tmp_path):
    output_file = tmp_path / "invalid_bids.json"
    with pytest.raises(ValueError, match="Unsupported state"):
        await run_scraper_pipeline(state="NY", output_path=output_file, max_pages=1, headless=True)
