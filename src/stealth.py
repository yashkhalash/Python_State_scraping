import random
import asyncio
from playwright.async_api import Playwright, BrowserContext

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)

async def create_stealth_context(
    playwright: Playwright, headless: bool = True
) -> BrowserContext:
    """
    Launches a Chromium browser instance with stealth launch arguments and context parameters.
    """
    browser = await playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=DEFAULT_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
    )
    await context.add_init_script(STEALTH_SCRIPT)
    return context

async def random_delay(min_seconds: float = 1.2, max_seconds: float = 3.0) -> None:
    """
    Pauses execution asynchronously for a random duration between min_seconds and max_seconds.
    """
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))
