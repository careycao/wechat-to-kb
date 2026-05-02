"""URL normalization utilities for KB deduplication."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_TRACKING_PARAMS = frozenset({
    # UTM parameters
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "utm_id", "utm_reader", "utm_place",
    # WeChat tracking
    "from", "chksm", "scene", "subscene", "sessionid", "ascene",
    "pass_ticket", "wx_header", "clickthrough_count",
    # Common tracking
    "ref", "referrer", "source", "via",
})

_WECHAT_ID_RE = re.compile(r"/s/([A-Za-z0-9_-]+)")


def normalize_url(url: str) -> str:
    """Normalize URL for deduplication.

    - Remove tracking/UTM parameters
    - Remove fragment (#)
    - Unify scheme to https
    - Remove trailing slashes
    - WeChat (mp.weixin.qq.com): keep only /s/<id> path
    """
    url = url.strip()
    if not url:
        return url

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    scheme = "https"
    netloc = parsed.netloc.lower()
    path = parsed.path

    # WeChat special case: only keep canonical /s/<id>
    if netloc == "mp.weixin.qq.com":
        m = _WECHAT_ID_RE.search(path)
        if m:
            path = f"/s/{m.group(1)}"
            return urlunparse((scheme, netloc, path, "", "", ""))

    # Remove tracking parameters
    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
        query = urlencode(filtered, doseq=True)
    else:
        query = ""

    # Remove fragment; strip trailing slash (preserve "/" for root)
    path = path.rstrip("/") or "/"

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))
