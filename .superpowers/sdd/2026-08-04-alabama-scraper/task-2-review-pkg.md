# Review Package for Task 2

## Commits
5dee1f5 feat: integrate Alabama state support into CLI and test pipeline

## Stat
 run_scraper.py    | 62 +++++++++++++++++++++++++++++++++++++++++++++++++++++++
 tests/test_e2e.py | 36 ++++++++++++++++++++++++++++++++
 2 files changed, 98 insertions(+)

## Diff
```diff
diff --git a/run_scraper.py b/run_scraper.py
new file mode 100644
index 0000000..5135989
--- /dev/null
+++ b/run_scraper.py
@@ -0,0 +1,62 @@
+import argparse
+import asyncio
+from pathlib import Path
+from playwright.async_api import async_playwright
+from src.stealth import create_stealth_context
+from src.scrapers.georgia import GeorgiaScraper
+from src.scrapers.alabama import AlabamaScraper
+from src.storage import StorageManager
+
+async def run_scraper_pipeline(state: str, output_path: Path, max_pages: int = 1, headless: bool = True) -> int:
+    """
+    Execute end-to-end procurement scraping pipeline for a given state.
+    """
+    state_upper = state.upper()
+    if state_upper not in ["GA", "AL"]:
+        raise ValueError(f"Unsupported state: {state}")
+
+    output_path = Path(output_path)
+    print(f"Starting scraper for state: {state_upper}")
+    
+    async with async_playwright() as p:
+        context = await create_stealth_context(p, headless=headless)
+        
+        if state_upper == "GA":
+            scraper = GeorgiaScraper()
+        elif state_upper == "AL":
+            scraper = AlabamaScraper()
+            
+        bids = await scraper.scrape_all_bids(context, max_pages=max_pages)
+        await context.close()
+
+        storage = StorageManager()
+        storage.upsert_bids(output_path, bids)
+        print(f"Successfully scraped and saved {len(bids)} bids to {output_path}")
+        return len(bids)
+
+def main():
+    parser = argparse.ArgumentParser(description="State Procurement Data Scraper")
+    parser.add_argument("--state", type=str, default="GA", help="Target state code (e.g. GA, AL)")
+    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
+    parser.add_argument("--max-pages", type=int, default=1, help="Max search result pages to scrape")
+    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
+    
+    args = parser.parse_args()
+    
+    state_code = args.state.upper()
+    if args.output:
+        output_path = Path(args.output)
+    else:
+        output_path = Path(f"data/output/{state_code.lower()}_bids.json")
+    
+    asyncio.run(
+        run_scraper_pipeline(
+            state=state_code,
+            output_path=output_path,
+            max_pages=args.max_pages,
+            headless=not args.headed,
+        )
+    )
+
+if __name__ == "__main__":
+    main()
diff --git a/tests/test_e2e.py b/tests/test_e2e.py
new file mode 100644
index 0000000..17a1fd7
--- /dev/null
+++ b/tests/test_e2e.py
@@ -0,0 +1,36 @@
+import json
+import pytest
+from pathlib import Path
+from run_scraper import run_scraper_pipeline, main
+
+@pytest.mark.asyncio
+async def test_run_scraper_pipeline_ga(tmp_path):
+    output_file = tmp_path / "georgia_bids.json"
+    count = await run_scraper_pipeline(state="GA", output_path=output_file, max_pages=1, headless=True)
+    assert isinstance(count, int)
+    assert count >= 0
+    assert output_file.exists()
+    
+    with open(output_file, "r", encoding="utf-8") as f:
+        data = json.load(f)
+    assert isinstance(data, list)
+    assert len(data) == count
+
+@pytest.mark.asyncio
+async def test_run_scraper_pipeline_alabama(tmp_path):
+    output_file = tmp_path / "alabama_bids.json"
+    count = await run_scraper_pipeline(state="AL", output_path=output_file, max_pages=1, headless=True)
+    assert isinstance(count, int)
+    assert count >= 0
+    assert output_file.exists()
+    
+    with open(output_file, "r", encoding="utf-8") as f:
+        data = json.load(f)
+    assert isinstance(data, list)
+    assert len(data) == count
+
+@pytest.mark.asyncio
+async def test_run_scraper_pipeline_invalid_state(tmp_path):
+    output_file = tmp_path / "invalid_bids.json"
+    with pytest.raises(ValueError, match="Unsupported state"):
+        await run_scraper_pipeline(state="NY", output_path=output_file, max_pages=1, headless=True)

```
