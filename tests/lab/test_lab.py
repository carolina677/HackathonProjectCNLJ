import httpx

SLUGS = ["ssr", "spa", "multistep", "summary-redirect", "login", "captcha-open-docs"]


async def test_every_slug_serves(lab_server):
    async with httpx.AsyncClient() as c:
        for s in SLUGS:
            r = await c.get(lab_server.url(s))
            assert r.status_code == 200, s


async def test_spa_shell_has_little_text(lab_server):
    from selectolax.lexbor import LexborHTMLParser as HTMLParser

    async with httpx.AsyncClient() as c:
        html = (await c.get(lab_server.url("spa"))).text
    tree = HTMLParser(html)
    for n in tree.css("script"):
        n.decompose()
    assert len(tree.body.text(strip=True)) < 500


async def test_spa_api_lists_tenders(lab_server):
    async with httpx.AsyncClient() as c:
        j = (await c.get(lab_server.url("spa") + "api/tenders", params={"offset": 0, "limit": 10})).json()
    assert len(j["items"]) == 10 and j["total"] == 23


def test_truth_unique_ids(lab_server):
    t = lab_server.truth("ssr")
    assert len(t) == 23 and len({x.id for x in t}) == 23
    assert t[0].id == "LAB-0001"
    assert all(x.submissionDeadline.tzinfo is not None for x in t)
    assert all(len(c) == 8 for x in t for c in (x.cpvCodes or []))


async def test_ssr_paginates_ten_per_page(lab_server):
    async with httpx.AsyncClient() as c:
        p1 = (await c.get(lab_server.url("ssr"))).text
        p3 = (await c.get(lab_server.url("ssr"), params={"page": 3})).text
    assert p1.count('class="tender-row"') == 10
    assert p3.count('class="tender-row"') == 3


async def test_captcha_portal_docs_download(lab_server):
    t = lab_server.truth("captcha-open-docs")[0]
    async with httpx.AsyncClient() as c:
        list_html = (await c.get(lab_server.url("captcha-open-docs"))).text
        assert "Roboter" in list_html
        r = await c.get(lab_server.doc_url("captcha-open-docs", t.id, 1))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


async def test_multistep_zip(lab_server):
    import io
    import zipfile

    t = lab_server.truth("multistep")[0]
    async with httpx.AsyncClient() as c:
        r = await c.get(lab_server.base + f"/lab/multistep/zip/{t.id}.zip")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == sorted(d.fileName for d in t.documents)


async def test_login_portal_has_password_form(lab_server):
    async with httpx.AsyncClient() as c:
        html = (await c.get(lab_server.url("login"))).text
    assert 'type="password"' in html and "tender-row" not in html


async def test_summary_links_to_platform(lab_server):
    async with httpx.AsyncClient() as c:
        html = (await c.get(lab_server.url("summary-redirect"))).text
    assert "/lab/ssr/" in html
