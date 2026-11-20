import pytest

from tests.scripts_loader import load_script


def test_summarise_counts_and_projection():
    rows = [
        {"country": "DE", "url": "a", "state": "serving", "reason": None, "tenders": 10, "docs": 30, "usd": 0.10},
        {"country": "FR", "url": "b", "state": "serving", "reason": None, "tenders": 5, "docs": 5, "usd": 0.20},
        {"country": "NL", "url": "c", "state": "failed", "reason": "login", "tenders": 0, "docs": 0, "usd": 0.30},
    ]
    s = load_script("run_testset").summarise(rows)
    assert s["serving"] == 2 and s["total"] == 3
    assert s["tenders"] == 15 and s["documents"] == 35
    assert s["usd_total"] == pytest.approx(0.60)
    assert s["usd_per_portal"] == pytest.approx(0.20)  # failed portals cost money too
    assert s["usd_4500"] == pytest.approx(900.0)
