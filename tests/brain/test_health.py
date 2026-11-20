from datetime import datetime, timezone

from tests.brain.fakes import FakeJev, NoJudge
from watchtower.brain.health import fill_rates, health_check, spot_check

T = {"id": "a", "title": "t", "buyerName": "b", "publicationDate": "x", "submissionDeadline": "y"}


def _many(n, **over):
    return [{**T, "id": str(i), **over} for i in range(n)]


def test_healthy():
    h = health_check(20, _many(20), [], prev_fill=fill_rates(_many(20)))
    assert h.ok and h.reason is None


def test_zero_is_unhealthy_only_if_prev_positive():
    assert not health_check(5, [], []).ok
    assert health_check(0, [], []).ok


def test_count_drop_unhealthy():
    h = health_check(20, _many(8), [])
    assert not h.ok and "8" in h.reason and "20" in h.reason


def test_small_portal_count_drop_tolerated():
    assert health_check(4, _many(1), []).ok


def test_required_field_null_spike():
    prev = fill_rates(_many(20))
    h = health_check(20, _many(20, submissionDeadline=None), [], prev_fill=prev)
    assert not h.ok and "submissionDeadline" in h.reason


def test_selector_miss_reason_in_words():
    h = health_check(20, [], ["list selector matched no items on https://x/"])
    assert not h.ok and "layout" in h.reason.lower()


def test_http_error_reason():
    h = health_check(20, [], ["HTTP 503 on https://x/"])
    assert not h.ok and "503" in h.reason


async def test_spot_check():
    page = "<html><body>Titel Neubau Kita Angebotsfrist 15.12.2026 12:00</body></html>"
    assert await spot_check({"id": "a", "title": "Neubau Kita"}, page, FakeJev())
    assert not await spot_check({"id": "a", "title": "Schwimmbad"}, page, FakeJev())
    assert await spot_check({"id": "a", "title": "Schwimmbad"}, page, NoJudge())
