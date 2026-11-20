"""AI Vergabeplattform / NetServer by Administration Intelligence AG (many German Länder and municipalities)."""

from urllib.parse import urlsplit

from ..fetch import Page
from ..recipe.dsl import Recipe

NAME = "netserver"
SIGNATURE = [
    lambda p: "PublicationSearchControllerServlet" in p.html or "PublicationSearchControllerServlet" in p.url,
    lambda p: "Administration Intelligence" in p.html or "/NetServer/" in (p.final_url or p.url),
]


def _prefix(url: str) -> str:
    u = urlsplit(url)
    path = u.path.split("/PublicationSearchControllerServlet")[0].rstrip("/")
    if "/NetServer" in path:
        path = path[: path.index("/NetServer") + len("/NetServer")]
    return f"{u.scheme}://{u.netloc}{path}"


def template(page: Page) -> Recipe:
    base = _prefix(page.final_url or page.url)
    return Recipe.model_validate({
        "platform": NAME, "source": "html", "country": "DE",
        "list_url": f"{base}/PublicationSearchControllerServlet?function=SearchPublications"
                    "&Gesetzesgrundlage=All&Category=InvitationToTender&thContext=publications",
        "pagination": {"kind": "offset", "param": "Start", "start": 0, "step": 50},
        "item": "tr.publicationDetail",
        "fields": {
            "id": {"selector": ".", "attr": "data-oid"},
            "title": {"selector": "td.tender"},
            "referenceNumber": {"selector": "td.tender", "regex": r"\(([^()]+)\)\s*$"},
            "buyerName": {"selector": "td.tenderAuthority"},
            "publicationDate": {"selector": "td:first-child", "normalizer": "date"},
            "procedureType": {"selector": "td.tenderType", "normalizer": "procedure"},
            "submissionDeadline": {"selector": "td.tenderDeadline", "normalizer": "datetime"},
            "url": {"selector": ".", "attr": "data-oid", "normalizer": "url",
                    "template": base + "/PublicationControllerServlet?function=Detail&TOID={value}"
                                       "&Category=InvitationToTender"},
        },
        "detail": {"source": "html", "url_field": "url", "fields": {
            "contractNature": {"label": "d) Art des Auftrags", "normalizer": "nature"}}},
        "documents": [{"selector": "a[href*='TenderingProcedureDetails']", "attr": "href", "follow": True},
                      {"selector": "a[href*='_DownloadTenderDocuments']", "attr": "href"}],
    })
