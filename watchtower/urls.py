from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    """Same portal written differently → same string (adds https:// if missing, drops fragment/trailing slash)."""
    raw = url.strip()
    if not raw:
        raise ValueError("not a web address")
    if "://" not in raw:
        raw = "https://" + raw
    p = urlsplit(raw)
    scheme = p.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("not a web address")
    host = (p.hostname or "").lower()
    if not host or any(c.isspace() for c in host) or ("." not in host and host not in ("localhost", "::1")):
        raise ValueError("not a web address")
    netloc = f"[{host}]" if ":" in host else host
    port = p.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"
    path = p.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, p.query, ""))


def host_key(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
