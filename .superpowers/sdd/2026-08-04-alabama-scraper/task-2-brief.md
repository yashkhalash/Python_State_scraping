# Task 2 Brief: Integrate Alabama Scraper into CLI & End-to-End Test

**Plan File:** `docs/superpowers/plans/2026-08-04-alabama-scraper.md`
**Task Number:** 2

## Requirements
Integrate `AlabamaScraper` into `run_scraper.py` CLI pipeline and update `tests/test_e2e.py`.

### Files to touch:
- Modify `run_scraper.py`
- Modify `tests/test_e2e.py`

### Specifications:
1. In `run_scraper.py`:
   - Update `run_scraper_pipeline` signature and body to accept `state: str` supporting `"GA"` and `"AL"` (case-insensitive).
   - If `state.upper() == "GA"`, instantiate `GeorgiaScraper()`.
   - If `state.upper() == "AL"`, instantiate `AlabamaScraper()`.
   - Raise `ValueError(f"Unsupported state: {state}")` if state is not in `["GA", "AL"]`.
   - In `main()`: default `--output` to `data/output/{state.lower()}_bids.json` if `--output` is not explicitly provided.
2. In `tests/test_e2e.py`:
   - Add test `test_run_scraper_pipeline_alabama(tmp_path)` using `@pytest.mark.asyncio`.
   - Call `run_scraper_pipeline(state="AL", output_path=tmp_path / "alabama_bids.json", max_pages=1, headless=True)`.
   - Assert output file exists and returns `count >= 0`.
3. Follow TDD:
   - First add failing test in `tests/test_e2e.py`.
   - Run `.venv/bin/pytest tests/test_e2e.py -v` (fails with unsupported state AL).
   - Update `run_scraper.py`.
   - Re-run `.venv/bin/pytest tests/test_e2e.py -v` (passes).
   - Commit with message `feat: integrate Alabama state support into CLI and test pipeline`.

### Report File:
Write task report to `.superpowers/sdd/2026-08-04-alabama-scraper/task-2-report.md`.
