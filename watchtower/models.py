"""The API contract from the Patterno brief (appendix "Exact answers")."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer, StringConstraints


class PortalState(StrEnum):
    integrating = "integrating"
    serving = "serving"
    healing = "healing"
    failed = "failed"


class RunKind(StrEnum):
    read = "read"
    repair = "repair"


class RunResult(StrEnum):
    ok = "ok"
    failed = "failed"


class TenderStatus(StrEnum):
    open = "open"
    closed = "closed"
    withdrawn = "withdrawn"


class ProcedureType(StrEnum):
    open = "open"
    restricted = "restricted"
    negotiated = "negotiated"
    other = "other"


class ContractNature(StrEnum):
    works = "works"
    services = "services"
    supplies = "supplies"


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("datetime must carry an offset")
    return v


AwareDatetime = Annotated[
    datetime,
    AfterValidator(_aware),
    PlainSerializer(lambda v: v.isoformat(), when_used="json"),
]
Cpv = Annotated[str, StringConstraints(pattern=r"^\d{8}$")]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]


class Document(BaseModel):
    fileName: str
    sizeBytes: int
    sha256: str
    path: str


class Place(BaseModel):
    postalCode: str | None = None
    city: str | None = None
    countryCode: CountryCode | None = None


REQUIRED_FIELDS = (
    "id", "url", "title", "buyerName", "publicationDate", "submissionDeadline",
    "status", "documents", "lastChangedAt",
)
OPTIONAL_FIELDS = (
    "referenceNumber", "description", "procedureType", "contractNature",
    "cpvCodes", "placeOfPerformance",
)


class Tender(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: str
    url: str
    title: str | None
    buyerName: str | None
    publicationDate: date | None
    submissionDeadline: AwareDatetime | None
    status: TenderStatus
    documents: list[Document]
    lastChangedAt: AwareDatetime
    referenceNumber: str | None = None
    description: str | None = None
    procedureType: ProcedureType | None = None
    contractNature: ContractNature | None = None
    cpvCodes: list[Cpv] | None = None
    placeOfPerformance: Place | None = None


def tender_json(t: Tender) -> dict[str, Any]:
    """Required fields always present (null allowed); optional fields omitted when absent."""
    data = t.model_dump(mode="json")
    return {k: v for k, v in data.items() if k in REQUIRED_FIELDS or v is not None}


class PortalOut(BaseModel):
    id: str
    url: str
    state: PortalState
    reason: str | None
    tenderCount: int
    lastSuccessAt: AwareDatetime | None


class RunOut(BaseModel):
    id: str
    kind: RunKind
    startedAt: AwareDatetime
    finishedAt: AwareDatetime | None
    result: RunResult | None
