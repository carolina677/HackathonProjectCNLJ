"""e-Vergabe, the federal German procurement platform (evergabe-online.de, Apache Wicket).

Documents are left out: each file link is a Wicket listener tied to one server-side page state, and the
site's load balancer bounces requests between backends, so the link answers with the page instead of the
file. A missing document list is better than a wrong one.
"""

from urllib.parse import urlsplit

from ..fetch import Page
from ..recipe.dsl import Recipe

NAME = "evergabe"
SIGNATURE = [
    lambda p: "evergabe-online" in urlsplit(p.final_url or p.url).netloc or "evergabe-online.de" in p.html,
    lambda p: "tenderdetails.html?id=" in p.html and 'id="datatable"' in p.html,
]


_STATES = ("Baden-Württemberg|Bayern|Berlin|Brandenburg|Bremen|Hamburg|Hessen|Mecklenburg-Vorpommern|Niedersachsen|"
           "Nordrhein-Westfalen|Rheinland-Pfalz|Saarland|Sachsen|Sachsen-Anhalt|Schleswig-Holstein|Thüringen")
STATE_PREFIX = rf"^\s*(?:(?:{_STATES}):\s*)?(.+?)\s*$"  # "Sachsen-Anhalt: Stadt Genthin" → "Stadt Genthin"


def template(page: Page) -> Recipe:
    u = urlsplit(page.final_url or page.url)
    base = f"{u.scheme}://{u.netloc}"
    return Recipe.model_validate({
        "platform": NAME, "source": "html", "country": "DE",
        "list_url": f"{base}/search.html",
        # Only the first page (the 10 newest): next-page links are Wicket state links that answer 403 over
        # plain HTTP. The engine then reports the list as incomplete, so no tender is closed for being unseen.
        "pagination": {"kind": "next_link", "next_selector": "table#datatable thead a.next", "max_pages": 1},
        "item": "table#datatable tbody tr",
        "fields": {
            "id": {"selector": "td:first-child a", "attr": "href", "regex": r"id=(\d+)"},
            "title": {"selector": "td:first-child"},
            "referenceNumber": {"selector": "td:nth-child(2)"},
            "procedureType": {"selector": "td:nth-child(5)", "normalizer": "procedure"},
            "url": {"selector": "td:first-child a", "attr": "href", "regex": r"id=(\d+)",
                    "template": base + "/tenderdetails.html?id={value}"},
        },
        "detail": {"source": "html", "url_field": "url", "fields": {
            "buyerName": {"label": "Vergabestelle", "regex": STATE_PREFIX},
            "publicationDate": {"label": "Veröffentlichungsdatum", "normalizer": "date"},
            "submissionDeadline": {"label": "Abgabefrist Angebot", "normalizer": "datetime"},
        }},
    })
