"""Pagination by form post-back (PRADO as on Atexo, ASP.NET __doPostBack). No GET URL reaches page 2."""

from urllib.parse import parse_qs

from watchtower.fetch import Page
from watchtower.recipe.dsl import Recipe
from watchtower.recipe.engine import run_recipe

PAGES = 3


def prado(n: int) -> str:
    rows = "".join(f"<div class='item'><span class='id'>T{n}-{i}</span><span class='t'>Lot {n}.{i}</span></div>"
                   for i in range(4))
    nxt = (f"<a id='ctl0_Pager_ctl2' href='javascript:;//ctl0_Pager_ctl2' title='suivante'>›</a>" if n < PAGES else "")
    return (f"<html><body><form method='post' action='/list?AllCons'>"
            f"<input type='hidden' name='PRADO_PAGESTATE' value='state{n}'><input name='q' value='x'>"
            f"<input type='submit' name='go' value='Go'>{rows}{nxt}</form></body></html>")


def aspnet(n: int) -> str:
    rows = "".join(f"<tr class='r'><td class='id'>A{n}-{i}</td><td class='t'>Item {i}</td></tr>" for i in range(3))
    nxt = (f"<a class='next' href=\"javascript:__doPostBack('grid$pager$next','Page${n + 1}')\">Next</a>"
           if n < PAGES else "")
    return (f"<html><body><form method='post' action='./list.aspx' id='aspnetForm'>"
            f"<input type='hidden' name='__VIEWSTATE' value='vs{n}'><input type='hidden' name='__EVENTTARGET' value=''>"
            f"<input type='hidden' name='__EVENTARGUMENT' value=''><table>{rows}</table>{nxt}</form></body></html>")


class PostbackFetcher:
    def __init__(self, render):
        self.render, self.posts = render, []

    async def get(self, url):
        return Page(url=url, final_url=url, status=200, html=self.render(1), headers={})

    async def get_json(self, url, params=None):
        raise AssertionError("no json")

    async def post(self, url, data):
        self.posts.append((url, dict(data)))
        state = data.get("PRADO_PAGESTATE") or data.get("__VIEWSTATE")
        n = int(state[-1])
        target = data.get("PRADO_POSTBACK_TARGET") or data.get("__EVENTTARGET")
        assert target, "post-back without an event target"
        return Page(url=url, final_url=url, status=200, html=self.render(n + 1), headers={})


async def test_prado_postback_reads_all_pages():
    f = PostbackFetcher(prado)
    r = Recipe.model_validate({
        "source": "html", "list_url": "https://p.example/list?AllCons", "item": "div.item",
        "pagination": {"kind": "postback", "next_selector": "a[id$='Pager_ctl2']"},
        "fields": {"id": {"selector": "span.id"}, "title": {"selector": "span.t"},
                   "url": {"selector": "span.id", "template": "https://p.example/t/{value}", "normalizer": "url"}}})
    ts = await run_recipe(r, f, errors=[])
    assert len(ts) == 12 and ts[-1]["id"] == "T3-3"
    url, data = f.posts[0]
    assert url == "https://p.example/list?AllCons"
    assert data["PRADO_POSTBACK_TARGET"] == "ctl0$Pager$ctl2" and data["q"] == "x" and "go" not in data


async def test_aspnet_dopostback_reads_all_pages():
    f = PostbackFetcher(aspnet)
    r = Recipe.model_validate({
        "source": "html", "list_url": "https://a.example/list.aspx", "item": "tr.r",
        "pagination": {"kind": "postback", "next_selector": "a.next"},
        "fields": {"id": {"selector": "td.id"}, "title": {"selector": "td.t"}}})
    ts = await run_recipe(r, f, errors=[])
    assert len(ts) == 9
    url, data = f.posts[1]
    assert url == "https://a.example/list.aspx"
    assert data["__EVENTTARGET"] == "grid$pager$next" and data["__EVENTARGUMENT"] == "Page$3"
