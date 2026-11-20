from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    """Same portal written differently → same string (case of scheme/host, fragment, trailing slash)."""
    p = urlsplit(url.strip())
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    port = p.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        host = f"{host}:{port}"
    path = p.path.rstrip("/")
    return urlunsplit((scheme, host, path, p.query, ""))


def host_key(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
