"""Atexo LocalTrust (French regional platforms such as Maximilien, PLACE, Mégalis). Server-rendered PRADO pages."""

from urllib.parse import urlsplit

from ..fetch import Page
from ..recipe.dsl import Recipe

NAME = "atexo"
SIGNATURE = [
    lambda p: "Entreprise.Entreprise" in p.html,
    lambda p: "atexo" in p.html.lower() or "item_consultation" in p.html,
]


def template(page: Page) -> Recipe:
    u = urlsplit(page.final_url or page.url)
    origin = f"{u.scheme}://{u.netloc}"
    return Recipe.model_validate({
        "platform": NAME, "source": "html", "country": "FR",
        "list_url": f"{origin}/?page=Entreprise.EntrepriseAdvancedSearch&AllCons",
        "pagination": {"kind": "none"},  # further pages are PRADO post-backs
        "item": "div.item_consultation",
        "fields": {
            "id": {"selector": "input[id$='_refCons']", "attr": "value"},
            "referenceNumber": {"selector": "div.objet-line div.small.pull-left"},
            "title": {"selector": "div.objet-line div.truncate span"},
            "description": {"selector": "div[id$='panelBlocObjet'] span.small"},
            "buyerName": {"selector": "div[id$='panelBlocDenomination'] span.small", "regex": r"^\s*([^(]+?)\s*(?:\(|$)"},
            "url": {"selector": "a[href*='/entreprise/consultation/']", "attr": "href",
                    "regex": r"^(.*?/consultation/\d+\?orgAcronyme=[A-Za-z0-9]+)", "normalizer": "url"},
            "publicationDate": {"selector": "div.cons_ref div.date", "normalizer": "date"},
            "submissionDeadline": {"selector": "div.cons_dateEnd div.cloture-line", "normalizer": "datetime"},
            "contractNature": {"selector": "div.cons_categorie", "normalizer": "nature"},
            "procedureType": {"selector": "div.cons_procedure abbr", "attr": "title", "normalizer": "procedure"},
        },
        "detail": {"source": "html", "url_field": "url", "fields": {}},
        "documents": [{"selector": "a[href*='EntrepriseDownloadReglement'], a[href*='EntrepriseDownloadComplement'], "
                                   "a[href*='telechargement/zip']", "attr": "href"}],
    })
