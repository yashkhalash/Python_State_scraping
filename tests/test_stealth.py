import pytest
import time
from playwright.async_api import async_playwright
from src.stealth import create_stealth_context, random_delay

@pytest.mark.asyncio
async def test_stealth_context_overrides():
    async with async_playwright() as p:
        context = await create_stealth_context(p, headless=True)
        page = await context.new_page()
        await page.goto("about:blank")
        
        # Check navigator.webdriver override
        webdriver_val = await page.evaluate("navigator.webdriver")
        assert webdriver_val is None or webdriver_val is False

        # Check navigator.plugins override
        plugins_len = await page.evaluate("navigator.plugins.length")
        assert plugins_len > 0

        # Check navigator.languages override
        languages = await page.evaluate("navigator.languages")
        assert "en-US" in languages

        # Check viewport size
        viewport = page.viewport_size
        assert viewport == {"width": 1920, "height": 1080}
        
        await context.close()

@pytest.mark.asyncio
async def test_random_delay():
    start = time.time()
    await random_delay(0.1, 0.3)
    elapsed = time.time() - start
    assert 0.08 <= elapsed <= 0.6
