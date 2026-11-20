from tests.helpers import SimpleFetcher
from watchtower.recipe.dsl import Recipe
from watchtower.recipe.engine import run_recipe

DETAIL_FIELDS = {
    "referenceNumber": {"label": "Vergabenummer"},
    "submissionDeadline": {"label": "Angebotsfrist", "normalizer": "datetime"},
    "procedureType": {"label": "Verfahrensart", "normalizer": "procedure"},
    "contractNature": {"label": "Art des Auftrags", "normalizer": "nature"},
    "cpvCodes": {"label": "CPV-Codes", "normalizer": "cpv_list"},
    "description": {"label": "Beschreibung"},
    "placeOfPerformance.postalCode": {"label": "Erfüllungsort", "regex": r"(\d{5})"},
    "placeOfPerformance.city": {"label": "Erfüllungsort", "regex": r"\d{5}\s+([^,]+)"},
    "placeOfPerformance.countryCode": {"label": "Erfüllungsort", "normalizer": "country"},
}


def ssr_recipe(lab, slug="ssr", docs=None):
    return Recipe.model_validate({
        "source": "html", "list_url": lab.url(slug), "country": "DE",
        "pagination": {"kind": "page_param", "param": "page", "start": 1},
        "item": "tr.tender-row",
        "fields": {
            "id": {"selector": "td.nr"},
            "title": {"selector": "td a"},
            "url": {"selector": "td a", "attr": "href", "normalizer": "url"},
            "buyerName": {"selector": "td.buyer"},
            "publicationDate": {"selector": "td.published", "normalizer": "date"},
        },
        "detail": {"source": "html", "url_field": "url", "fields": DETAIL_FIELDS},
        "documents": docs or [{"selector": "ul.documents a", "attr": "href"}],
    })


def spa_recipe(lab):
    return Recipe.model_validate({
        "source": "json_api", "list_url": lab.url("spa") + "api/tenders", "country": "DE",
        "pagination": {"kind": "offset", "param": "offset", "start": 0, "step": 10},
        "item": "$.items[*]",
        "fields": {
            "id": {"selector": "$.id"}, "title": {"selector": "$.title"},
            "url": {"selector": "$.id", "template": lab.url("spa") + "tender/{value}", "normalizer": "url"},
            "buyerName": {"selector": "$.authority"},
            "publicationDate": {"selector": "$.publishedAt", "normalizer": "date"},
            "submissionDeadline": {"selector": "$.deadline", "normalizer": "datetime"},
            "status": {"selector": "$.cancelled", "normalizer": "status"},
            "cpvCodes": {"selector": "$.cpv", "normalizer": "cpv_list"},
        },
        "documents": [{"selector": "$.files[*].href", "attr": "value"}],
    })


async def test_engine_html_pagination(lab):
    tenders = await run_recipe(ssr_recipe(lab), SimpleFetcher())
    assert len(tenders) == 23 and tenders[0]["id"] == "LAB-0001"


async def test_engine_html_fields_match_truth(lab):
    got = {t["id"]: t for t in await run_recipe(ssr_recipe(lab), SimpleFetcher(), limit=5)}
    for want in lab.truth("ssr")[:5]:
        t = got[want.id]
        assert t["submissionDeadline"] == want.submissionDeadline
        assert t["publicationDate"] == want.publicationDate
        assert t["buyerName"] == want.buyerName and t["title"] == want.title
        assert t["url"] == want.url
        assert t.get("cpvCodes", []) == (want.cpvCodes or [])
        assert t["procedureType"] == want.procedureType
        assert t["placeOfPerformance"]["postalCode"] == want.placeOfPerformance.postalCode
        assert t["placeOfPerformance"]["countryCode"] == "DE"
        assert len(t["_doc_urls"]) == len(want.documents)


async def test_engine_json_api_offset(lab):
    tenders = await run_recipe(spa_recipe(lab), SimpleFetcher())
    assert len(tenders) == 23
    want = lab.truth("spa")[0]
    assert tenders[0]["submissionDeadline"] == want.submissionDeadline
    assert tenders[0]["_doc_urls"][0].startswith("http")


async def test_engine_multistep_documents(lab):
    r = ssr_recipe(lab, "multistep", docs=[{"selector": "a.tab", "attr": "href", "follow": True},
                                           {"selector": "a.download", "attr": "href"}])
    t = (await run_recipe(r, SimpleFetcher(), limit=1))[0]
    assert t["_doc_urls"] == [lab.base + "/lab/multistep/zip/LAB-0001.zip"]


async def test_engine_next_link_pagination(lab):
    lab.mutate("ssr", "pagination_style")
    r = ssr_recipe(lab)
    r.pagination.kind = "next_link"
    r.pagination.next_selector = "a[rel=next]"
    assert len(await run_recipe(r, SimpleFetcher())) == 23


async def test_engine_reports_selector_misses(lab):
    lab.mutate("ssr", "rename_classes")
    errors: list[str] = []
    tenders = await run_recipe(ssr_recipe(lab), SimpleFetcher(), errors=errors)
    assert tenders == [] and any("matched no items" in e for e in errors)


async def test_engine_mask_nulls_fields(lab):
    r = ssr_recipe(lab)
    t = (await run_recipe(r, SimpleFetcher(), limit=1, mask={"buyerName", "description"}))[0]
    assert t["buyerName"] is None and "description" not in t


async def test_withdrawn_detected_without_status_rule(lab):
    lab.mutate("ssr", "withdraw_one")
    ts = await run_recipe(ssr_recipe(lab), SimpleFetcher())
    truth = {t.id: t.status for t in lab.truth("ssr")}
    assert [t["id"] for t in ts if t["status"] == "withdrawn"] == [i for i, s in truth.items() if s == "withdrawn"]
    assert sum(t["status"] == "withdrawn" for t in ts) == 1


async def test_hooks_list_first_then_each_tender(lab):
    calls = []

    async def on_list(items, errors):
        calls.append(("list", len(items)))
        return True

    async def on_tender(t):
        calls.append(("tender", t["id"]))

    ts = await run_recipe(ssr_recipe(lab), SimpleFetcher(), on_list=on_list, on_tender=on_tender)
    assert calls[0] == ("list", 23) and [c[1] for c in calls[1:]] == [t["id"] for t in ts]


async def test_on_list_false_stops_before_details(lab):
    f = SimpleFetcher()

    async def stop(items, errors):
        return False

    ts = await run_recipe(ssr_recipe(lab), f, on_list=stop)
    assert len(ts) == 23 and f.calls <= 4  # 3 list pages + the empty 4th; no detail pages


def test_label_does_not_match_a_longer_word():
    from selectolax.lexbor import LexborHTMLParser
    from watchtower.recipe.engine import find_by_label

    root = LexborHTMLParser("<nav><span>Vergabestellen</span></nav><dl><dt>Vergabestelle:</dt><dd>Stadt Köln</dd>"
                            "<dt>Frist (Ende)</dt><dd>x</dd></dl>").root
    assert find_by_label(root, "Vergabestelle") == "Stadt Köln"
