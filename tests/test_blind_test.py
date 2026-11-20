from tests.scripts_loader import load_script
from watchtower.lab.blind import blind_spec
from watchtower.lab.scorer import Score


def test_row_describes_the_portal_and_the_result():
    bt = load_script("blind_test")
    spec = blind_spec(12)
    s = Score(attach=0.9, quality=0.75, wrong_values=3, missing_values=5)
    row = bt.blind_row(12, {"state": "serving", "reason": None}, s, docs=20, usd=0.0123, seconds=95.0)
    assert row["seed"] == 12 and row["state"] == "serving"
    assert row["portal"] == f"{spec.layout}/{spec.source}/{spec.lang}/{spec.date_fmt}/{spec.paging}"
    assert (row["attach"], row["quality"], row["wrong"], row["docs"]) == (0.9, 0.75, 3, 20)


def test_row_without_score_has_no_quality():
    row = load_script("blind_test").blind_row(5, {"state": "failed", "reason": "x"}, None, 0, 0.0, 10.0)
    assert row["attach"] == 0.0 and row["quality"] is None


def test_summary_averages_only_scored_portals():
    bt = load_script("blind_test")
    rows = [{"state": "serving", "attach": 1.0, "quality": 0.8, "usd": 0.02},
            {"state": "failed", "attach": 0.0, "quality": None, "usd": 0.04}]
    s = bt.summarise(rows)
    assert s == {"serving": 1, "total": 2, "attach_avg": 0.5, "quality_avg": 0.8, "usd_per_portal": 0.03}
