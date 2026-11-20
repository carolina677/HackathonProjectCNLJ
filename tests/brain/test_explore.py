import pytest

from tests.brain.fakes import FakeJev
from watchtower.brain.explore import explore
from watchtower.fetch import Fetcher, close_browser
from watchtower.scheduler import HostLimiter


@pytest.fixture
async def env(tmp_path):
    f = Fetcher("p", HostLimiter(min_interval=0.0), snapshot_dir=tmp_path)
    yield f, FakeJev()
    await close_browser()


async def test_ssr_list_and_details(lab, env):
    f, jev = env
    ex = await explore(lab.url("ssr"), f, jev, portal_id="p")
    assert ex.blocked is None and ex.list_page.final_url.startswith(lab.url("ssr"))
    assert len(ex.detail_pages) >= 2 and all("/tender/" in d.url for d in ex.detail_pages)
    assert ex.docs_reachable


async def test_summary_page_followed_to_platform(lab, env):
    f, jev = env
    ex = await explore(lab.url("summary-redirect"), f, jev, portal_id="p")
    assert ex.list_page.final_url.startswith(lab.url("ssr"))
    assert ex.path[0] == lab.url("summary-redirect")


async def test_json_api_detected(lab, env):
    f, jev = env
    ex = await explore(lab.url("spa"), f, jev, portal_id="p")
    assert ex.json_candidates and "api/tenders" in ex.json_candidates[0].url


async def test_login_blocked(lab, env):
    f, jev = env
    ex = await explore(lab.url("login"), f, jev, portal_id="p")
    assert ex.blocked == "login" and ex.list_page is None


async def test_captcha_but_docs_download_not_blocked(lab, env):
    f, jev = env
    ex = await explore(lab.url("captcha-open-docs"), f, jev, portal_id="p")
    assert ex.blocked is None and ex.docs_reachable and ex.list_page is not None


async def test_multistep_documents_reachable(lab, env):
    f, jev = env
    ex = await explore(lab.url("multistep"), f, jev, portal_id="p")
    assert ex.docs_reachable and any("unterlagen" in u for u in ex.path)


async def test_site_down_reports_error(lab, env):
    f, jev = env
    lab.mutate("ssr", "site_down")
    ex = await explore(lab.url("ssr"), f, jev, portal_id="p")
    assert ex.list_page is None and ex.error and "503" in ex.error


async def test_detail_and_document_blocks_reported():
    from contextlib import asynccontextmanager
    from watchtower.fetch import Page

    class FakeDocResp:
        def __init__(self, status=200, ct="text/html", chunks=()):
            self.status_code, self.headers, self._chunks = status, {"content-type": ct}, chunks

        async def aiter_bytes(self):
            for c in self._chunks:
                yield c

    class ScriptedFetcher:
        def __init__(self, pages, streams=None, posts=None):
            self.pages, self.streams, self.posts = pages, streams or {}, posts or {}

        async def open(self, url):
            return self.pages[url]

        async def post(self, url, data):
            return self.posts[url]

        @asynccontextmanager
        async def stream(self, url):
            yield self.streams.get(url, FakeDocResp(404))

    list_html = ("<html><body><h1>Aktuelle Ausschreibungen</h1><p>5 Treffer</p>"
                 "<a href='https://p.example/tender/1'>Bauleistung Strasse 1</a></body></html>")
    list_page = Page("https://p.example/", "https://p.example/", 200, list_html)

    # 1. Detail page requires login (HTTP 401) -> blocked = login
    f1 = ScriptedFetcher({
        "https://p.example/": list_page,
        "https://p.example/tender/1": Page("https://p.example/tender/1", "https://p.example/tender/1", 401, ""),
    })
    ex1 = await explore("https://p.example/", f1, FakeJev())
    assert ex1.blocked == "login"

    # 2. Detail page public, document tab shows login form -> blocked = login
    detail_ok = Page("https://p.example/tender/1", "https://p.example/tender/1", 200,
                     "<html><body><p>Interne Nr. 1</p><a href='https://p.example/tender/1/unterlagen'>Unterlagen</a></body></html>")
    doc_login = Page("https://p.example/tender/1/unterlagen", "https://p.example/tender/1/unterlagen", 200,
                     "<html><body><form method='post'><input type='password' name='pw'></form></body></html>")
    f2 = ScriptedFetcher({
        "https://p.example/": list_page,
        "https://p.example/tender/1": detail_ok,
        "https://p.example/tender/1/unterlagen": doc_login,
    })
    ex2 = await explore("https://p.example/", f2, FakeJev())
    assert ex2.blocked == "login" and not ex2.docs_reachable

    # 3. Detail page public, document tab shows captcha -> blocked = captcha
    doc_captcha = Page("https://p.example/tender/1/unterlagen", "https://p.example/tender/1/unterlagen", 200,
                       "<html><body><p>Bitte bestätigen Sie, dass Sie kein Roboter sind.</p></body></html>")
    f3 = ScriptedFetcher({
        "https://p.example/": list_page,
        "https://p.example/tender/1": detail_ok,
        "https://p.example/tender/1/unterlagen": doc_captcha,
    })
    ex3 = await explore("https://p.example/", f3, FakeJev())
    assert ex3.blocked == "captcha" and not ex3.docs_reachable

    # 4. Non-login search form gets submitted to reach the list page
    search_page = Page("https://p.example/search", "https://p.example/search", 200,
                       "<html><body><h1>Suchformular</h1><form method='post' action='/results'>"
                       "<input name='q' value='all'></form></body></html>")
    f4 = ScriptedFetcher(
        pages={"https://p.example/search": search_page, "https://p.example/tender/1": detail_ok},
        posts={"https://p.example/results": list_page},
        streams={"https://p.example/tender/1/unterlagen": FakeDocResp(200, "application/pdf", [b"%PDF-1.4"])},
    )
    ex4 = await explore("https://p.example/search", f4, FakeJev())
    assert ex4.blocked is None and ex4.list_page is list_page and ex4.docs_reachable
