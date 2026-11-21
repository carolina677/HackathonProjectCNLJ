"""Tender documents: stream to disk, hash, unpack ZIPs (one nested level), never hold a file in memory."""

import hashlib
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from .fetch import PortalCancelled
from .models import Document

log = logging.getLogger("watchtower.documents")
MAX_BYTES = 250 * 1024 ** 2
CHUNK = 1024 * 256


class TooLarge(Exception):
    pass


def filename_from(url: str, headers: dict) -> str:
    cd = {k.lower(): v for k, v in headers.items()}.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if m:
        return m.group(1).strip()
    name = unquote(PurePosixPath(urlsplit(url).path).name)
    return name or "document"


OFFICE_EXT = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods", ".odp", ".odg", ".jar", ".epub")


def _is_zip(path: Path, name: str) -> bool:
    """A real archive to unpack. Office/ODF files are ZIP containers too and must stay whole."""
    if name.lower().endswith(OFFICE_EXT):
        return False
    with open(path, "rb") as f:
        magic = f.read(4) == b"PK\x03\x04"
    if not (magic or name.lower().endswith(".zip")) or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return False
    return not ({"[Content_Types].xml", "mimetype", "META-INF/MANIFEST.MF"} & names)


class _Store:
    def __init__(self, repo, data_dir: Path, portal_id: str, tender_id: str):
        self.repo, self.portal_id, self.tender_id = repo, portal_id, tender_id
        self.root = Path(data_dir) / "files"
        self.tmp = Path(data_dir) / "tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.seen: set[str] = set()
        self.out: list[Document] = []

    def tmpfile(self) -> Path:
        fd, p = tempfile.mkstemp(dir=self.tmp)
        os.close(fd)
        return Path(p)

    def reuse(self, docs: list[Document]) -> None:
        """Documents the portal confirmed unchanged (HTTP 304): served as stored, nothing downloaded."""
        for d in docs:
            if d.sha256 not in self.seen:
                self.seen.add(d.sha256)
                self.out.append(d)

    async def keep(self, tmp: Path, name: str, sha: str, size: int) -> None:
        if sha in self.seen:
            tmp.unlink(missing_ok=True)
            return
        self.seen.add(sha)
        dest = self.root / sha[:2] / sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            tmp.unlink(missing_ok=True)
        else:
            shutil.move(tmp, dest)
        fid = await self.repo.save_document(portal_id=self.portal_id, tender_id=self.tender_id, file_name=name,
                                            size_bytes=size, sha256=sha, stored_path=str(dest))
        self.out.append(Document(fileName=name, sizeBytes=size, sha256=sha, path=f"/files/{fid}"))


def _copy_hashed(src, dst: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(dst, "wb") as out:
        while chunk := src.read(CHUNK):
            n += len(chunk)
            if n > MAX_BYTES:
                raise TooLarge
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest(), n


def _safe_entry(name: str) -> str | None:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.name:
        return None
    return p.name


async def _unpack(store: _Store, zpath: Path, depth: int) -> None:
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = _safe_entry(info.filename)
            if not name:
                log.warning("rejected unsafe zip entry %r", info.filename)
                continue
            if info.file_size > MAX_BYTES:
                continue
            tmp = store.tmpfile()
            try:
                with z.open(info) as src:
                    sha, size = _copy_hashed(src, tmp)
            except TooLarge:
                tmp.unlink(missing_ok=True)
                continue
            if depth == 0 and _is_zip(tmp, name):
                try:
                    await _unpack(store, tmp, depth + 1)
                finally:
                    tmp.unlink(missing_ok=True)
                continue
            await store.keep(tmp, name, sha, size)


_CAPTCHA = re.compile(rb"captcha|g-recaptcha|h-captcha|hcaptcha|cf-turnstile|friendlycaptcha", re.I)
_LOGIN = re.compile(rb"type=[\"']?password|\b(log ?in|sign ?in|anmelden|einloggen|connexion|se connecter|"
                    rb"iniciar sesi|inloggen|aanmelden|zaloguj|accedi)\b", re.I)


def not_a_document(path: Path, headers: dict, size: int) -> str | None:
    """Why a download is not a tender document, or None. Portals often answer HTTP 200 with a login page."""
    if size == 0:
        return "empty file instead of a document"
    h = {k.lower(): v for k, v in headers.items()}
    if "attachment" in h.get("content-disposition", "").lower():
        return None
    with open(path, "rb") as f:
        head = f.read(65536)
    start = head.lstrip()[:512].lower()
    if not (start.startswith(b"<!doctype html") or b"<html" in start):
        return None  # PDFs, ZIPs, office files, XML: whatever the content-type claims
    if _CAPTCHA.search(head):
        return "captcha page instead of a document"
    if _LOGIN.search(head):
        return "login page instead of a document"
    return "HTML page instead of a document"


def revalidation_headers(headers: dict) -> dict[str, str]:
    """Conditional-request headers that ask the portal whether a document changed since this download."""
    h = {k.lower(): v for k, v in headers.items()}
    out = {}
    if h.get("etag"):
        out["If-None-Match"] = h["etag"]
    if h.get("last-modified"):
        out["If-Modified-Since"] = h["last-modified"]
    return out


async def fetch_documents(portal_id: str, tender_id: str, urls: list[str], fetcher, *, repo,
                          data_dir: Path, errors: list[str] | None = None,
                          cache: dict[str, tuple[dict, list[Document]]] | None = None) -> list[Document]:
    """Download, hash and store each document.

    `cache` (url -> (conditional headers, documents), updated in place) lets a later read skip unchanged
    files: the document is requested again with If-None-Match / If-Modified-Since and only a 304 reuses the
    stored copy. A portal that sends neither ETag nor Last-Modified gets a full download, and the hash
    decides whether the document changed. The same URL never short-circuits on its own."""
    store = _Store(repo, data_dir, portal_id, tender_id)
    for url in urls:
        prev = cache.get(url) if cache is not None else None
        cond = prev[0] if prev else {}
        start = len(store.out)
        tmp = store.tmpfile()
        try:
            async with (fetcher.stream(url, headers=cond) if cond else fetcher.stream(url)) as resp:
                status = getattr(resp, "status_code", 200)
                if status == 304 and prev:
                    store.reuse(prev[1])
                    tmp.unlink(missing_ok=True)
                    continue
                if status >= 300:
                    raise IOError(f"HTTP {status}")
                headers = dict(resp.headers)
                declared = {k.lower(): v for k, v in headers.items()}.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                    raise TooLarge
                h, n = hashlib.sha256(), 0
                with open(tmp, "wb") as out:
                    async for chunk in resp.aiter_bytes():
                        n += len(chunk)
                        if n > MAX_BYTES:
                            raise TooLarge
                        h.update(chunk)
                        out.write(chunk)
            why = not_a_document(tmp, headers, n)
            if why:
                raise IOError(why)
            name = filename_from(url, headers)
            if _is_zip(tmp, name):
                try:
                    await _unpack(store, tmp, 0)
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                await store.keep(tmp, name, h.hexdigest(), n)
            if cache is not None:
                cache[url] = (revalidation_headers(headers), store.out[start:])
        except PortalCancelled:
            tmp.unlink(missing_ok=True)
            raise
        except TooLarge:
            log.info("skipped %s: larger than 250 MB", url)
            tmp.unlink(missing_ok=True)
        except Exception as e:
            if cache is not None:
                cache.pop(url, None)
            log.warning("document %s failed: %s", url, e)
            if errors is not None:
                errors.append(f"document {url}: {e}")
            tmp.unlink(missing_ok=True)
    return store.out
