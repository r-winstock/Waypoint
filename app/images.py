"""Representative photos for card backgrounds (Trips/Places/Cities), sourced
from Wikipedia and cached to local disk rather than re-fetched on every page
load - see CachedImage in models.py for why a query can be cached as "no
image found" too, not just a successful result.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.db import DATA_DIR
from app.models import CachedImage

USER_AGENT = "Waypoint/0.1 (self-hosted personal timeline; contact rwinstock@hotmail.com)"
WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

IMAGES_DIR = DATA_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

_STOPWORDS = {"the", "and", "of", "in", "at", "on", "for", "de", "la", "le", "el", "an", "a"}


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "unknown"


def _significant_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in _STOPWORDS}


def _plausible_match(query: str, title: str) -> bool:
    """Wikipedia's search is a loose full-text match, not a lookup - for an
    arbitrary business/place name with no real Wikipedia page, the "best"
    result it returns can be something with zero real connection to the
    query (confirmed live: "Collins McNicholas - Recruitment And HR
    Services", an Irish recruitment agency, matched to "Blackwater
    (company)", the US military contractor, on generic word overlap alone).
    Requires the query and matched title to actually share a real word
    before accepting the match - showing no photo is strictly better than
    showing a wrong, possibly reputationally awkward one."""
    query_words = _significant_words(query)
    title_words = _significant_words(title)
    return bool(query_words & title_words)


def _wikipedia_search_title(query: str) -> str | None:
    try:
        resp = httpx.get(
            WIKIPEDIA_SEARCH_URL,
            params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        resp.raise_for_status()
        results = resp.json().get("query", {}).get("search", [])
        if not results:
            return None
        title = results[0]["title"]
        return title if _plausible_match(query, title) else None
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


def _wikipedia_lead_image(title: str) -> str | None:
    try:
        resp = httpx.get(
            WIKIPEDIA_SUMMARY_URL.format(title=quote(title)),
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return (data.get("originalimage") or {}).get("source") or (data.get("thumbnail") or {}).get("source")
    except (httpx.HTTPError, ValueError):
        return None


def _download_image(url: str, key: str) -> str | None:
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    ext = Path(url.split("?")[0]).suffix
    if not ext or len(ext) > 6:
        ext = ".jpg"
    path = IMAGES_DIR / f"{key}{ext}"
    path.write_bytes(resp.content)
    return str(path)


def get_or_fetch_image(session: Session, query: str, force: bool = False) -> CachedImage:
    """Get-or-create a cached image for this query. force=True re-attempts
    the fetch even over a previously cached result (the "refresh" action)."""
    key = _slugify(query)
    cached = session.get(CachedImage, key)
    if cached is not None and not force:
        return cached

    title = _wikipedia_search_title(query)
    image_url = _wikipedia_lead_image(title) if title else None
    local_path = _download_image(image_url, key) if image_url else None

    if cached is None:
        cached = CachedImage(key=key, query=query)
        session.add(cached)
    cached.query = query
    cached.source_url = image_url
    cached.image_path = local_path
    cached.found = local_path is not None
    cached.fetched_at = int(time.time())
    session.flush()
    return cached
