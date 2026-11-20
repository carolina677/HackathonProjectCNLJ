import socket

import httpx
import pytest

from watchtower.fetch import Page
from watchtower.pipeline import open_start, unreachable_reason


def _dns_error(url):
    return httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known",
                              request=httpx.Request("GET", url))


class Fake:
    def __init__(self, ok: set[str]):
        self.ok, self.tried = ok, []

    async def open(self, url):
        self.tried.append(url)
        if url in self.ok:
            return Page(url, url, 200, "<html>ok</html>")
        raise _dns_error(url)


async def test_bare_host_that_does_not_exist_falls_back_to_www():
    f = Fake({"https://www.evergabe-online.de"})
    page, used = await open_start(f, "https://evergabe-online.de")
    assert used == "https://www.evergabe-online.de" and page.status == 200


async def test_working_url_is_used_as_is():
    f = Fake({"https://x.de"})
    _, used = await open_start(f, "https://x.de")
    assert used == "https://x.de" and f.tried == ["https://x.de"]


async def test_neither_resolves_raises_the_original_error():
    with pytest.raises(httpx.ConnectError):
        await open_start(Fake(set()), "https://nothing-here.example")


def test_dns_failure_is_explained_in_words():
    r = unreachable_reason(_dns_error("https://nothing-here.example"), "https://nothing-here.example")
    assert "does not exist" in r and "nothing-here.example" in r and "Errno" not in r
