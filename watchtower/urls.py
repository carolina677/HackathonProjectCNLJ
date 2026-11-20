from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    """Same portal written differently → same string (adds https:// if missing, drops fragment/trailing slash)."""
    raw = url.strip()
    if not raw or any(c.isspace() for c in raw):
        raise ValueError("not a web address")
    has_scheme = "://" in raw
    if not has_scheme:
        raw = "https://" + raw
    p = urlsplit(raw)
    scheme = p.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("not a web address")
    host = (p.hostname or "").lower()
    if not host or (not has_scheme and "." not in host and host not in ("localhost", "::1")):
        raise ValueError("not a web address")
    netloc = f"[{host}]" if ":" in host else host
    port = p.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"
    path = p.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, p.query, ""))


def host_key(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def www_variant(url: str) -> str | None:
    """https://example.de/x → https://www.example.de/x, for bare public host names only."""
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if not host or host.startswith("www.") or host == "localhost" or "." not in host:
        return None
    if host.replace(".", "").isdigit() or ":" in host:
        return None
    netloc = "www." + p.netloc.split("@")[-1]
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
