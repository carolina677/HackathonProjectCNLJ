from datetime import date, timedelta

from watchtower.models import ContractNature, ProcedureType
from watchtower.recipe.normalize import (map_nature, map_procedure, parse_cpv, parse_date, parse_deadline,
                                         parse_country)


def test_deadline_local_time_gets_offset_with_dst():
    assert parse_deadline("15.12.2026 12:00", "DE").isoformat() == "2026-12-15T12:00:00+01:00"
    assert parse_deadline("15.07.2026 12:00", "DE").isoformat() == "2026-07-15T12:00:00+02:00"
    assert parse_deadline("2026-12-15T12:00:00Z", "DE").utcoffset() == timedelta(0)
    assert parse_deadline("2026-12-15T12:00:00", "NL").isoformat() == "2026-12-15T12:00:00+01:00"


def test_deadline_german_long_form():
    assert parse_deadline("15. Dezember 2026, 12:00 Uhr", "DE").isoformat() == "2026-12-15T12:00:00+01:00"


def test_deadline_portugal_and_poland_timezones():
    assert parse_deadline("15/12/2026 12:00", "PT").isoformat() == "2026-12-15T12:00:00+00:00"
    assert parse_deadline("15.12.2026 12:00", "PL").isoformat() == "2026-12-15T12:00:00+01:00"


def test_date_only_deadline_is_none():
    assert parse_deadline("15.12.2026", "DE") is None


def test_dates_multilingual():
    assert parse_date("3 décembre 2026", "FR") == date(2026, 12, 3)
    assert parse_date("03/12/2026", "ES") == date(2026, 12, 3)
    assert parse_date("01.11.2026", "DE") == date(2026, 11, 1)
    assert parse_date("2026-11-01", "DE") == date(2026, 11, 1)


def test_cpv():
    assert parse_cpv("45000000-7, 71200000-0") == ["45000000", "71200000"]
    assert parse_cpv("Bauarbeiten") == []
    assert parse_cpv(["45000000-7", "45000000"]) == ["45000000"]


def test_unparseable_returns_none():
    assert parse_deadline("siehe Unterlagen", "DE") is None
    assert parse_date("", "DE") is None


def test_procedure_mapping():
    assert map_procedure("Nicht offenes Verfahren") == ProcedureType.restricted
    assert map_procedure("Offenes Verfahren") == ProcedureType.open
    assert map_procedure("Procédure négociée") == ProcedureType.negotiated
    assert map_procedure("Innovationspartnerschaft") == ProcedureType.other
    assert map_procedure("xyz") is None


def test_nature_mapping():
    assert map_nature("Bauleistung") == ContractNature.works
    assert map_nature("Servicios") == ContractNature.services
    assert map_nature("Fournitures") == ContractNature.supplies


def test_country():
    assert parse_country("52062 Aachen, Deutschland") == "DE"
    assert parse_country("FR") == "FR"
