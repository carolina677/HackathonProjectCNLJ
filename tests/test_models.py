from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from watchtower.models import PortalState, Tender, TenderStatus, tender_json

BASE = dict(
    id="A1",
    url="https://p/1",
    title="Neubau Kita",
    buyerName="Stadt Aachen",
    publicationDate=date(2026, 11, 20),
    submissionDeadline=datetime(2026, 12, 9, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")),
    status=TenderStatus.open,
    documents=[],
    lastChangedAt=datetime(2026, 11, 20, 9, tzinfo=timezone.utc),
)


def test_optional_fields_omitted_required_null():
    t = Tender(**{**BASE, "title": None})
    j = tender_json(t)
    assert j["title"] is None
    assert "cpvCodes" not in j and "description" not in j
    assert j["submissionDeadline"] == "2026-12-09T10:00:00+01:00"
    assert j["publicationDate"] == "2026-11-20"
    assert j["lastChangedAt"] == "2026-11-20T09:00:00+00:00"


def test_cpv_rejects_check_digit():
    with pytest.raises(ValidationError):
        Tender(**BASE, cpvCodes=["45000000-7"])


def test_optional_field_kept_when_set():
    j = tender_json(Tender(**BASE, cpvCodes=["45000000"], procedureType="open"))
    assert j["cpvCodes"] == ["45000000"] and j["procedureType"] == "open"


def test_enum_values_match_brief():
    assert {s.value for s in PortalState} == {"integrating", "serving", "healing", "failed"}
    assert {s.value for s in TenderStatus} == {"open", "closed", "withdrawn"}
