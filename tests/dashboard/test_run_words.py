from watchtower.dashboard.routes import run_words


def test_first_run_stopped_by_a_restart_says_so_not_could_not_learn():
    r = {"kind": "read", "result": "failed", "detail": "interrupted by restart"}
    assert run_words(r, first=True) == "Read stopped: interrupted by restart"


def test_first_run_that_failed_without_a_reason_could_not_learn():
    assert run_words({"kind": "read", "result": "failed", "detail": None}, first=True) == "Could not learn the portal"


def test_first_run_ok_learned():
    assert run_words({"kind": "read", "result": "ok", "detail": None}, first=True) == "Learned the portal"


def test_repair_stopped_by_a_restart_does_not_ask_for_a_human():
    r = {"kind": "repair", "result": "failed", "detail": "interrupted by restart"}
    assert run_words(r, first=False) == "Repair stopped: interrupted by restart"
