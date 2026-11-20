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


def test_www_variant_only_for_bare_public_hosts():
    from watchtower.urls import www_variant

    assert www_variant("https://evergabe-online.de/search") == "https://www.evergabe-online.de/search"
    assert www_variant("https://www.evergabe-online.de") is None
    assert www_variant("http://localhost:8090/lab/ssr") is None
    assert www_variant("http://127.0.0.1:8090/x") is None
    assert www_variant("https://vergabe.nrw.de") == "https://www.vergabe.nrw.de"
