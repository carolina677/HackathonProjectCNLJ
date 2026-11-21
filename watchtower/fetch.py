"""Network access. Every request is polite (per-host limiter) and cancellable.

httpx for plain pages, JSON and downloads; one shared headless Chromium for pages that need JavaScript.
The browser records every JSON response so the agent can find a portal's hidden API.
"""

import asyncio
import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from . import events
from .urls import host_key

log = logging.getLogger("watchtower.fetch")
UA = "WatchtowerBot/1.0 (+procurement monitoring; polite: 1 req/s)"
HEADERS = {"User-Agent": UA, "Accept-Language": "de,en;q=0.8,fr;q=0.6,es;q=0.5,nl;q=0.5"}


class PortalCancelled(Exception):
    pass


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    headers: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        from .recipe.engine import visible_text

        return visible_text(self.html)


@dataclass
class CapturedJson:
    url: str
    method: str
    status: int
    body: Any
    post_data: str | None = None


@dataclass
class Link:
    text: str
    href: str
    context: str = ""


@dataclass
class RenderedPage(Page):
    screenshot: bytes = b""
    json_responses: list[CapturedJson] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)


def needs_js(page: Page) -> bool:
    from .recipe.engine import visible_text

    txt = visible_text(page.html)
    low = page.html.lower()
    spa_root = any(f'id="{r}"' in low or f"id='{r}'" in low for r in ("app", "root", "__next", "__nuxt"))
    return len(txt) < 500 or (spa_root and len(txt) < 1500)


def extract_links(html: str, base: str) -> list[Link]:
    from selectolax.lexbor import LexborHTMLParser

    out, seen = [], set()
    for a in LexborHTMLParser(html).css("a[href]"):
        href = a.attributes.get("href") or ""
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        u = urljoin(base, href)
        if u in seen:
            continue
        seen.add(u)
        parent = a.parent
        ctx = parent.text(separator=" ", strip=True)[:160] if parent else ""
        out.append(Link(a.text(separator=" ", strip=True)[:160], u, ctx))
    return out


# --- shared clients ----------------------------------------------------------
_http: httpx.AsyncClient | None = None
_pw = None
_browser = None
_browser_lock = asyncio.Lock()


_http_loop = None


def http() -> httpx.AsyncClient:
    """One shared client per event loop (a client is bound to the loop that created it)."""
    global _http, _http_loop
    loop = asyncio.get_running_loop()
    if _http is None or _http.is_closed or _http_loop is not loop:
        _http = httpx.AsyncClient(headers=HEADERS, follow_redirects=False, timeout=httpx.Timeout(30, read=120))
        _http_loop = loop
    return _http


async def browser():
    global _pw, _browser
    async with _browser_lock:
        if _browser is None or not _browser.is_connected():
            from playwright.async_api import async_playwright

            _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    return _browser


async def close_browser() -> None:
    global _pw, _browser, _http
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:
            pass
    _browser = _pw = None
    if _http is not None:
        await _http.aclose()
        _http = None


MAX_REDIRECTS = 20  # same as httpx; e-Vergabe bounces ~11 times through its cookie check
BROWSER_SKIPPED = {"image", "font", "media", "stylesheet"}


def route_policy(resource_type: str) -> str:
    """Inside the browser: skip what a tender list never needs; everything else waits for the limiter."""
    return "abort" if resource_type in BROWSER_SKIPPED else "throttle"


class Fetcher:
    def __init__(self, portal_id: str, limiter, cancelled: Callable[[], bool] = lambda: False,
                 snapshot_dir: Path | None = None):
        self.portal_id, self.limiter, self.cancelled = portal_id, limiter, cancelled
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        self.requests = 0
        self.on_screenshot = None  # optional async (png, url) -> None, display only (Jev Vision)

    async def _polite(self, url: str) -> None:
        if self.cancelled():
            raise PortalCancelled(self.portal_id)
        await self.limiter.acquire(host_key(url))
        if self.cancelled():
            raise PortalCancelled(self.portal_id)
        self.requests += 1

    async def _send(self, req: httpx.Request, stream: bool = False) -> httpx.Response:
        """Follow redirects by hand so every hop waits for the per-host limiter."""
        for _ in range(MAX_REDIRECTS + 1):
            await self._polite(str(req.url))
            r = await http().send(req, stream=True)
            nxt = r.next_request if r.is_redirect else None
            if nxt is None:
                if not stream:
                    await r.aread()
                    await r.aclose()
                return r
            await r.aclose()
            req = nxt
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=req)

    async def get(self, url: str) -> Page:
        t0 = time.monotonic()
        r = await self._send(http().build_request("GET", url))
        page = Page(url=url, final_url=str(r.url), status=r.status_code, html=r.text, headers=dict(r.headers))
        events.emit(self.portal_id, "page", {"url": url, "status": r.status_code, "via": "http",
                                             "ms": int((time.monotonic() - t0) * 1000)})
        return page

    async def post(self, url: str, data: dict) -> Page:
        """Form post-back for pagination only (never logins): same politeness as a GET."""
        t0 = time.monotonic()
        r = await self._send(http().build_request("POST", url, data=data))
        page = Page(url=url, final_url=str(r.url), status=r.status_code, html=r.text, headers=dict(r.headers))
        events.emit(self.portal_id, "page", {"url": url, "status": r.status_code, "via": "post",
                                             "ms": int((time.monotonic() - t0) * 1000)})
        return page

    async def get_json(self, url: str, params: dict | None = None) -> Any:
        r = await self._send(http().build_request("GET", url, params=params, headers={"Accept": "application/json"}))
        events.emit(self.portal_id, "page", {"url": str(r.url), "status": r.status_code, "via": "json"})
        r.raise_for_status()
        return r.json()

    async def post_json(self, url: str, body: Any = None, params: dict | None = None) -> Any:
        r = await self._send(http().build_request("POST", url, params=params, json=body,
                                                  headers={"Accept": "application/json"}))
        events.emit(self.portal_id, "page", {"url": str(r.url), "status": r.status_code, "via": "json-post"})
        r.raise_for_status()
        return r.json()

    @asynccontextmanager
    async def stream(self, url: str, headers: dict | None = None):
        r = await self._send(http().build_request("GET", url, headers=headers), stream=True)
        try:
            yield r
        finally:
            await r.aclose()

    async def render(self, url: str, wait_ms: int = 1500) -> RenderedPage:
        await self._polite(url)
        t0 = time.monotonic()
        b = await browser()
        ctx = await b.new_context(user_agent=UA, viewport={"width": 1280, "height": 860}, locale="de-DE")
        captured: list[CapturedJson] = []
        pending: list[asyncio.Task] = []

        async def route(r):
            if route_policy(r.request.resource_type) == "abort":
                return await r.abort()
            if r.request.url != url:
                try:
                    await self._polite(r.request.url)
                except PortalCancelled:
                    return await r.abort()
            await r.continue_()

        async def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if "json" in ct and resp.request.resource_type in ("xhr", "fetch"):
                    body = await resp.json()
                    try:
                        post_data = resp.request.post_data if resp.request.method.upper() != "GET" else None
                    except Exception:
                        post_data = None
                    captured.append(CapturedJson(resp.url, resp.request.method, resp.status, body, post_data))
            except Exception:
                pass

        try:
            await ctx.route("**/*", route)
            page = await ctx.new_page()
            page.on("response", lambda resp: pending.append(asyncio.create_task(on_response(resp))))
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await page.wait_for_timeout(wait_ms)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            html = await page.content()
            shot = await page.screenshot(type="jpeg", quality=55)
            final = page.url
            status = resp.status if resp else 0
        finally:
            await ctx.close()
        rp = RenderedPage(url=url, final_url=final, status=status, html=html, headers={}, screenshot=shot,
                          json_responses=captured, links=extract_links(html, final))
        events.emit(self.portal_id, "page", {
            "url": url, "status": status, "via": "browser", "ms": int((time.monotonic() - t0) * 1000),
            "json_apis": [c.url for c in captured][:5],
            "screenshot": "data:image/jpeg;base64," + base64.b64encode(shot).decode()})
        if self.on_screenshot is not None:
            asyncio.get_running_loop().create_task(self.on_screenshot(shot, url))
        return rp

    async def open(self, url: str) -> Page:
        """Plain HTTP first; the browser only when the page is a JavaScript shell."""
        p = await self.get(url)
        if p.status < 400 and needs_js(p):
            return await self.render(url)
        return p

    def snapshot(self, page: Page) -> str:
        if not self.snapshot_dir:
            return ""
        d = self.snapshot_dir / "snapshots" / self.portal_id
        d.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}.html"
        (d / name).write_text(page.html)
        if isinstance(page, RenderedPage) and page.json_responses:
            (d / (name + ".json")).write_text(json.dumps([c.body for c in page.json_responses[:3]])[:200000])
        return str(Path("snapshots") / self.portal_id / name)
