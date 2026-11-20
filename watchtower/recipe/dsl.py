"""Declarative recipe: what the LLM learns once per portal, and what the engine runs without a model."""

from typing import Literal

from pydantic import BaseModel, Field

Normalizer = Literal["text", "date", "datetime", "cpv_list", "country", "procedure", "nature", "status", "url", "int"]


class FieldRule(BaseModel):
    selector: str | None = None      # CSS/XPath (html) or JSONPath (json), relative to the item or page
    label: str | None = None         # html only: value next to a label like "Angebotsfrist"
    attr: str = "text"               # text | href | any attribute | value (json)
    regex: str | None = None         # first group (or whole match) is kept
    template: str | None = None      # e.g. "https://x/tender/{value}"
    normalizer: Normalizer = "text"


class Pagination(BaseModel):
    kind: Literal["none", "page_param", "offset", "next_link", "postback"] = "none"
    param: str | None = None         # postback: the event target, if it cannot be read from the "next" control
    start: int = 1
    step: int = 1
    next_selector: str | None = None
    max_pages: int = 200


class DetailRule(BaseModel):
    source: Literal["html", "json"] = "html"
    url_field: str = "url"
    url_template: str | None = None  # e.g. "https://x/api/tenders/{id}"
    fields: dict[str, FieldRule] = Field(default_factory=dict)


class DocStep(BaseModel):
    selector: str
    attr: str = "href"
    follow: bool = False             # open each match and continue with the next step
    needs_js: bool = False


class Recipe(BaseModel):
    source: Literal["html", "json_api"]
    list_url: str
    extra_params: dict[str, str | int] = Field(default_factory=dict)
    pagination: Pagination = Field(default_factory=Pagination)
    item: str
    fields: dict[str, FieldRule]
    detail: DetailRule | None = None
    documents: list[DocStep] = Field(default_factory=list)
    id_field: str = "id"
    country: str | None = None
    needs_js: bool = False
    platform: str | None = None
