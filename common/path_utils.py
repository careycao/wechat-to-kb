"""Path and URL helpers shared across collectors."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit


def redact_url_for_log(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    if not parts.scheme or not parts.netloc:
        return url
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def sanitize_url_input(url: str) -> str:
    sanitized = url.strip()
    for old, new in (("\\?", "?"), ("\\&", "&"), ("\\=", "="), ("\\#", "#")):
        sanitized = sanitized.replace(old, new)
    return sanitized


def load_urls_from_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
