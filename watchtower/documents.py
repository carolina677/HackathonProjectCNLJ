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


async def fetch_documents(portal_id: str, tender_id: str, urls: list[str], fetcher, *, repo,
                          data_dir: Path) -> list[Document]:
    store = _Store(repo, data_dir, portal_id, tender_id)
    for url in urls:
        tmp = store.tmpfile()
        try:
            async with fetcher.stream(url) as resp:
                if getattr(resp, "status_code", 200) >= 400:
                    raise IOError(f"HTTP {resp.status_code}")
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
            name = filename_from(url, headers)
            if _is_zip(tmp, name):
                try:
                    await _unpack(store, tmp, 0)
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                await store.keep(tmp, name, h.hexdigest(), n)
        except TooLarge:
            log.info("skipped %s: larger than 250 MB", url)
            tmp.unlink(missing_ok=True)
        except Exception as e:
            log.warning("document %s failed: %s", url, e)
            tmp.unlink(missing_ok=True)
    return store.out
