"""TenderNed (NL national platform). The Angular site sits on a public JSON API."""

from urllib.parse import urlsplit

from ..fetch import Page
from ..recipe.dsl import Recipe

NAME = "tenderned"
API = "https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties"
SIGNATURE = [lambda p: urlsplit(p.final_url or p.url).netloc.endswith("tenderned.nl")]


def template(page: Page) -> Recipe:
    return Recipe.model_validate({
        "platform": NAME, "source": "json_api", "country": "NL", "list_url": API,
        "extra_params": {"size": 100},
        # 147k publications, newest first: the newest 300 cover everything still open in practice.
        "pagination": {"kind": "page_param", "param": "page", "start": 0, "max_pages": 3},
        "item": "$.content[*]",
        "fields": {
            "id": {"selector": "$.publicatieId"},
            "title": {"selector": "$.aanbestedingNaam"},
            "buyerName": {"selector": "$.opdrachtgeverNaam"},
            "publicationDate": {"selector": "$.publicatieDatum", "normalizer": "date"},
            "submissionDeadline": {"selector": "$.sluitingsDatum", "normalizer": "datetime"},
            "procedureType": {"selector": "$.procedure.omschrijving", "normalizer": "procedure"},
            "contractNature": {"selector": "$.typeOpdracht.omschrijving", "normalizer": "nature"},
            "description": {"selector": "$.opdrachtBeschrijving"},
            "url": {"selector": "$.link.href", "normalizer": "url"},
            "status": {"selector": "$.isVroegtijdigeBeeindiging", "normalizer": "status"},
        },
        "detail": {"source": "json", "url_template": API + "/{id}/documenten", "fields": {}},
        "documents": [{"selector": "$.documenten[*].links.download.href", "attr": "value"}],
    })
