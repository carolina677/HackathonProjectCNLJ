import httpx

from watchtower.fetch import Page


class SimpleFetcher:
    """Direct httpx fetcher for tests (no rate limit, no browser)."""

    def __init__(self):
        self.calls = 0

    async def get(self, url: str) -> Page:
        self.calls += 1
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url)
        return Page(url=url, final_url=str(r.url), status=r.status_code, html=r.text, headers=dict(r.headers))

    async def get_json(self, url: str, params: dict | None = None):
        self.calls += 1
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()
