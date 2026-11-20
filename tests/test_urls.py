import pytest

from watchtower.urls import host_key, normalize_url


def test_normalize_same_portal():
    assert normalize_url("https://Vergabe.Example.de/list/#top") == normalize_url("https://vergabe.example.de/list")


def test_normalize_keeps_query():
    assert normalize_url("https://x.de/a?b=1") == "https://x.de/a?b=1"


def test_root_path_stable():
    assert normalize_url("https://x.de") == normalize_url("https://x.de/")


def test_host_key():
    assert host_key("https://Sub.X.de:443/a") == "sub.x.de"


def test_missing_scheme_gets_https():
    assert normalize_url("vergabe.example.de/ausschreibungen") == "https://vergabe.example.de/ausschreibungen"
    assert normalize_url("www.x.de") == normalize_url("https://www.x.de/")


def test_not_an_address_raises():
    for bad in ("", "just words", "ftp://x.de/a", "localhost_nope"):
        with pytest.raises(ValueError):
            normalize_url(bad)
