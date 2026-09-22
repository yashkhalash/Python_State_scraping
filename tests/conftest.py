"""
Shared test fixtures for Crawl4AI-based scraper tests.

Crawl4AI drives its own real Chromium instance and navigates to real URLs -
unlike raw Playwright, there is no `context.route()` hook we can attach to
intercept requests. So instead of route-mocking, tests spin up a tiny local
HTTP server that serves canned HTML for given paths, and point the scraper's
BASE_URL at http://127.0.0.1:<port>/... This keeps tests fully offline/
deterministic while exercising the real fetch + parse path end to end.
"""
import asyncio
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio

from src.crawler import get_crawler


class _RoutedHandler(BaseHTTPRequestHandler):
    routes = {}  # class-level, set per server instance via subclassing

    def log_message(self, format, *args):  # silence test noise
        pass

    def do_GET(self):
        path = self.path
        body = None
        for match, html in self.routes.items():
            if callable(match) and match(path):
                body = html
                break
        if body is None:
            body = self.routes.get("__default__")
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    do_POST = do_GET


@contextmanager
def local_html_server(routes: dict, default: str = None):
    """
    `routes`: mapping of {predicate(path) -> html_string}. The first matching
    predicate wins. `default` is served when nothing matches.
    """
    handler_routes = dict(routes)
    if default is not None:
        handler_routes["__default__"] = default

    class Handler(_RoutedHandler):
        pass

    Handler.routes = handler_routes

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest_asyncio.fixture
async def crawler():
    async with get_crawler(headless=True) as c:
        yield c
