import hashlib
import io
import zipfile
from contextlib import asynccontextmanager

import pytest

from watchtower import documents
from watchtower.documents import MAX_BYTES, fetch_documents


class FakeResp:
    def __init__(self, chunks, headers, status=200):
        self._chunks, self.headers, self.status_code = chunks, headers, status

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class FakeFetcher:
    def __init__(self, files: dict[str, tuple]):
        self.files = files

    @asynccontextmanager
    async def stream(self, url):
        body, headers = self.files[url]
        chunks = body if isinstance(body, list) else [body]
        yield FakeResp(chunks, headers)


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return buf.getvalue()


async def _fetch(repo, settings, files, urls):
    return await fetch_documents("p", "t", urls, FakeFetcher(files), repo=repo, data_dir=settings.data_dir)


def test_limit_is_250_mb():
    assert MAX_BYTES == 250 * 1024 ** 2


async def test_hash_and_path(repo, settings, client):
    payload = b"%PDF-1.4 hello"
    docs = await _fetch(repo, settings, {"https://x/dl?id=1": (payload, {"content-disposition": 'attachment; filename="a.pdf"'})},
                        ["https://x/dl?id=1"])
    assert len(docs) == 1
    d = docs[0]
    assert d.sha256 == hashlib.sha256(payload).hexdigest() and d.sizeBytes == len(payload)
    assert d.fileName == "a.pdf" and d.path.startswith("/files/")
    r = await client.get(d.path)
    assert r.content == payload


async def test_filename_from_url_when_no_header(repo, settings):
    docs = await _fetch(repo, settings, {"https://x/files/Leistungs%20verzeichnis.pdf": (b"x", {})},
                        ["https://x/files/Leistungs%20verzeichnis.pdf"])
    assert docs[0].fileName == "Leistungs verzeichnis.pdf"


async def test_zip_unpacked(repo, settings):
    z = _zip({"a.pdf": b"A", "sub/b.pdf": b"B"})
    docs = await _fetch(repo, settings, {"https://x/u.zip": (z, {})}, ["https://x/u.zip"])
    assert sorted(d.fileName for d in docs) == ["a.pdf", "b.pdf"]


async def test_nested_zip_one_level(repo, settings):
    inner = _zip({"c.pdf": b"C"})
    z = _zip({"a.pdf": b"A", "inner.zip": inner})
    docs = await _fetch(repo, settings, {"https://x/u.zip": (z, {})}, ["https://x/u.zip"])
    assert sorted(d.fileName for d in docs) == ["a.pdf", "c.pdf"]


async def test_zip_slip_rejected(repo, settings):
    z = _zip({"../evil.txt": b"E", "ok.pdf": b"K"})
    docs = await _fetch(repo, settings, {"https://x/u.zip": (z, {})}, ["https://x/u.zip"])
    assert [d.fileName for d in docs] == ["ok.pdf"]
    assert not (settings.data_dir.parent / "evil.txt").exists()
    assert not (settings.data_dir / "evil.txt").exists()


async def test_oversize_skipped(repo, settings, monkeypatch):
    monkeypatch.setattr(documents, "MAX_BYTES", 1000)
    files = {"https://x/big.pdf": ([b"x" * 600, b"x" * 600], {}),
             "https://x/declared.pdf": (b"x", {"content-length": "5000"})}
    docs = await _fetch(repo, settings, files, list(files))
    assert docs == []
    leftovers = [p for p in (settings.data_dir / "files").rglob("*") if p.is_file()] \
        if (settings.data_dir / "files").exists() else []
    assert leftovers == []


async def test_duplicates_collapsed(repo, settings):
    files = {"https://x/a.pdf": (b"same", {}), "https://x/a2.pdf": (b"same", {})}
    docs = await _fetch(repo, settings, files, list(files))
    assert len(docs) == 1


async def test_failed_download_does_not_raise(repo, settings):
    docs = await _fetch(repo, settings, {}, ["https://x/missing.pdf"])
    assert docs == []


def _docx() -> bytes:
    return _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})


async def test_office_file_kept_whole(repo, settings):
    files = {"https://x/Leistungsverzeichnis.docx": (_docx(), {}),
             "https://x/download?id=7": (_zip({"mimetype": b"application/vnd.oasis.opendocument.text"}), {})}
    docs = await _fetch(repo, settings, files, list(files))
    assert sorted(d.fileName for d in docs) == ["Leistungsverzeichnis.docx", "download"]


async def test_office_file_inside_zip_kept_whole(repo, settings):
    z = _zip({"a.pdf": b"A", "LV.xlsx": _docx()})
    docs = await _fetch(repo, settings, {"https://x/u.zip": (z, {})}, ["https://x/u.zip"])
    assert sorted(d.fileName for d in docs) == ["LV.xlsx", "a.pdf"]


async def test_zip_without_extension_still_unpacked(repo, settings):
    z = _zip({"a.pdf": b"A"})
    docs = await _fetch(repo, settings, {"https://x/DownloadTenderDocuments?oid=1": (z, {})},
                        ["https://x/DownloadTenderDocuments?oid=1"])
    assert [d.fileName for d in docs] == ["a.pdf"]
