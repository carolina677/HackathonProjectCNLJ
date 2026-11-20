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
