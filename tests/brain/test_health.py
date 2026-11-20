from datetime import datetime, timezone

from tests.brain.fakes import FakeJev, NoJudge
from watchtower.brain.health import fill_rates, health_check, pick_probe, spot_check

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
    page = "<html><body>Titel Neubau Kita Angebotsfrist 15.12.2026 12:00 Auftraggeber Stadt Aachen</body></html>"
    assert await spot_check({"id": "a", "title": "Neubau Kita", "buyerName": "Stadt Aachen"}, page, FakeJev())
    assert not await spot_check({"id": "a", "title": "Schwimmbad"}, page, FakeJev())
    assert not await spot_check({"id": "a", "title": "Neubau Kita", "buyerName": "Falsche Stadt"}, page, FakeJev())
    assert await spot_check({"id": "a", "title": "Schwimmbad"}, page, NoJudge())


def test_http_503_without_tenders_is_transient():
    h = health_check(20, [], ["HTTP 503 on https://p.example/list"])
    assert not h.ok and h.transient and "503" in h.reason and "not reachable" in h.reason


def test_timeout_without_tenders_is_transient():
    h = health_check(20, [], ["list request failed on https://p.example/api: ReadTimeout: timed out"])
    assert not h.ok and h.transient and "not reachable" in h.reason


def test_http_404_is_not_transient():
    h = health_check(20, [], ["HTTP 404 on https://p.example/list"])
    assert not h.ok and not h.transient


def test_layout_change_is_not_transient():
    h = health_check(20, [], ["list selector matched no items on https://p.example/list"])
    assert not h.ok and not h.transient


def test_probe_rotates_over_tenders_with_pages():
    raw = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    pages = {"a": "<p>", "c": "<p>"}
    assert [pick_probe(raw, pages, i)["id"] for i in range(3)] == ["a", "c", "a"]
    assert pick_probe(raw, {}, 0) is None


def test_document_fill_drop_is_unhealthy():
    items = _many(10)
    prev = fill_rates(items, doc_counts={str(i): 1 for i in range(10)})
    h = health_check(10, items, [], prev_fill=prev, doc_counts={str(i): 0 for i in range(10)})
    assert not h.ok and "documents" in h.reason


def test_document_403_block_is_unhealthy():
    items = _many(5)
    h = health_check(5, items, [], doc_counts={str(i): 0 for i in range(5)},
                     doc_errors=["document https://p.example/doc.pdf: HTTP 403"])
    assert not h.ok and "401/403" in h.reason
