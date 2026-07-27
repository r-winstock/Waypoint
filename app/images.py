"""Representative photos for card backgrounds (Trips/Places/Cities), sourced
from Wikipedia and cached to local disk rather than re-fetched on every page
load - see CachedImage in models.py for why a query can be cached as "no
image found" too, not just a successful result.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
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

# A category drilldown can render dozens of cards at once, each triggering a
# search + summary + image download - confirmed live that this got rowan
# rate-limited (HTTP 429) by Wikimedia Commons on the download step with no
# throttling at all. Same self-throttle pattern geocoding.py already uses
# for Nominatim, applied here across all three Wikipedia/Wikimedia calls.
MIN_INTERVAL_S = 0.4
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "unknown"


def _strip_diacritics(s: str) -> str:
    """"Kraków" -> "krakow" - the [a-z0-9]+ word regex below only matches
    ASCII, so an accented letter silently split a word in two instead of
    just being ignored (confirmed live: significant_words("Kraków") came
    back as {"krak"}, not {"krakow"} - the trailing "w" alone was too short
    to count as its own word). NFKD decomposes each accented character into
    its base letter plus a separate combining-mark codepoint, which the
    unicode category check then strips - leaving plain "krakow"."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if unicodedata.category(c) != "Mn")


def _significant_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _strip_diacritics(s).lower()) if len(w) > 2 and w not in _STOPWORDS}


_COUNTRY_DESCRIPTION_RE = re.compile(r"\bcountry\b", re.IGNORECASE)


def _is_country_article(description: str) -> bool:
    """Wikipedia's short description for every sovereign nation reliably
    contains the word "country" ("Country in East Asia", "Island country in
    the Atlantic Ocean", confirmed live across several real examples) - no
    call site here ever wants a nation's own article as a card photo, so
    this is always rejected outright rather than gated behind a flag.
    Found live: a Place literally named "Iceland" (a UK supermarket chain
    branch, its real OSM name) matched Wikipedia's search to the country
    Iceland's own article - a strong, confident title match with nothing
    about it looking wrong until you notice the flag isn't a shop."""
    return bool(_COUNTRY_DESCRIPTION_RE.search(description))


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


def _wikipedia_search_titles(query: str, limit: int = 6) -> list[str]:
    """Up to `limit` plausible candidate titles, in Wikipedia's own relevance
    order - not just the top hit. Searched as intitle:{query} rather than a
    bare full-text search - confirmed live that a bare search for a common
    place name ranks the disambiguation page and loosely-related content
    (e.g. "House of Windsor") above the real settlement article, whereas
    intitle: (restricted to titles that actually contain the query word)
    consistently surfaces the real "Place, Region" variants near the top
    (e.g. "Windsor, Berkshire", "Split, Croatia")."""
    _throttle()
    try:
        resp = httpx.get(
            WIKIPEDIA_SEARCH_URL,
            params={
                "action": "query", "list": "search", "format": "json", "srlimit": limit,
                "srsearch": f"intitle:{query}",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        resp.raise_for_status()
        results = resp.json().get("query", {}).get("search", [])
        return [r["title"] for r in results if _plausible_match(query, r["title"])]
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return []


@dataclass
class WikiPage:
    image_url: str
    description: str


def _wikipedia_page_info(title: str, require_coordinates: bool = False) -> WikiPage | None:
    """require_coordinates=True is for queries that are always a real place
    (a Trip/City lookup, or a Place's city fallback) - Wikipedia's search is
    a full-text match, not a lookup, and a bare common-word place name like
    "Reading" can correctly match the article titled exactly that (about
    the activity of reading, not the Berkshire town - confirmed live) on
    word-overlap alone. A genuine populated-place article always carries
    coordinates in its infobox; requiring them here rejects that whole
    class of wrong match without needing to guess which words are "really"
    place names. description ("City in Apulia, Italy") lets the caller
    prefer a candidate matching a disambiguation hint over an earlier-ranked
    one that doesn't."""
    _throttle()
    try:
        resp = httpx.get(
            WIKIPEDIA_SUMMARY_URL.format(title=quote(title)),
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        # A disambiguation page ("Split", "Windsor" as bare titles) never has
        # a meaningful lead image of its own regardless of query type - reject
        # it outright rather than only via the require_coordinates check,
        # which non-geo queries don't apply.
        if data.get("type") == "disambiguation":
            return None
        if require_coordinates and "coordinates" not in data:
            return None
        image_url = (data.get("originalimage") or {}).get("source") or (data.get("thumbnail") or {}).get("source")
        if not image_url:
            return None
        return WikiPage(image_url=image_url, description=data.get("description") or "")
    except (httpx.HTTPError, ValueError):
        return None


def _download_image(url: str, key: str) -> str | None:
    _throttle()
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


# A hint is a country name as the app itself uses it (from Nominatim, e.g.
# "United Kingdom") - but Wikipedia's own description text for a town almost
# never spells that out ("Town in Berkshire, England"), so a plain substring
# check silently never matches for these and the wrong-country candidate
# ranked higher by Wikipedia's own search order wins instead (confirmed
# live: "Windsor" resolved to Windsor, Ontario's skyline for a Berkshire
# visit). Not exhaustive - only covers hints likely for this app's own use
# (a personal, UK-based timeline) - a hint not listed here still gets a
# plain substring match, which most countries pass fine unprompted.
COUNTRY_HINT_ALIASES: dict[str, list[str]] = {
    "united kingdom": ["united kingdom", "england", "scotland", "wales", "northern ireland", "great britain", " uk"],
    "united states": ["united states", " usa", "u.s.a", "u.s."],
    "netherlands": ["netherlands", "holland"],
    "czechia": ["czechia", "czech republic"],
    "myanmar": ["myanmar", "burma"],
}


def _hint_matches(hint: str, description: str) -> bool:
    description_lower = description.lower()
    aliases = COUNTRY_HINT_ALIASES.get(hint.lower(), [hint.lower()])
    return any(alias in description_lower for alias in aliases)


def get_or_fetch_image(
    session: Session, query: str, force: bool = False, geo: bool = False, hint: str | None = None
) -> CachedImage:
    """Get-or-create a cached image for this query. force=True re-attempts
    the fetch even over a previously cached result (the "refresh" action).
    geo=True asserts this query is always a real place (a Trip/City name, or
    a Place's city fallback) - see _wikipedia_page_info for why that's
    treated differently from an arbitrary business/place name.

    hint is an optional disambiguator (a country name, for a city query).
    When given, it's a hard requirement, not a soft preference - a wrong-
    country photo (confirmed live: Windsor, Ontario shown for a Berkshire
    visit) is worse than no photo at all, the same principle already
    applied to plain business-name matches (see _plausible_match). Without
    a hint (the query has no known country context), the first plausible,
    coordinate-bearing candidate is used same as before."""
    key = _slugify(query)
    cached = session.get(CachedImage, key)
    if cached is not None and not force:
        return cached

    titles = _wikipedia_search_titles(query)

    fallback_page: WikiPage | None = None
    hint_confirmed_page: WikiPage | None = None
    for title in titles:
        page = _wikipedia_page_info(title, require_coordinates=geo)
        if page is None:
            continue
        if _is_country_article(page.description):
            continue
        if fallback_page is None:
            fallback_page = page
        if hint and _hint_matches(hint, page.description):
            hint_confirmed_page = page
            break
    chosen_page = hint_confirmed_page if hint else fallback_page
    image_url = chosen_page.image_url if chosen_page else None
    local_path = _download_image(image_url, key) if image_url else None

    if image_url and not local_path:
        # The search found a real image (image_url is a genuine Wikimedia
        # URL) but downloading it failed - confirmed live this was Wikimedia
        # rate-limiting (HTTP 429) under load, not a broken link. Caching
        # this as a permanent "not found" would poison it until a manual
        # refresh even though the photo genuinely exists - instead, return
        # an unsaved result so the next request retries the fetch from
        # scratch rather than trusting a transient failure forever.
        if cached is not None:
            return cached
        return CachedImage(key=key, query=query, source_url=image_url, found=False, fetched_at=int(time.time()))

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


# Only a handful of common image types are worth trusting the extension
# from - anything else falls back to .jpg (matches _download_image's own
# fallback), rather than trusting an arbitrary client-supplied filename.
_ALLOWED_UPLOAD_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def save_uploaded_image(session: Session, query: str, filename: str, content: bytes) -> CachedImage:
    """User-supplied photo, for when no automatic search finds the right
    one (or finds a confidently wrong one, e.g. Windsor Ontario for a
    Berkshire visit) - always overwrites any existing cached result for
    this query, the same as a manual refresh does."""
    key = _slugify(query)
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXT:
        ext = ".jpg"
    path = IMAGES_DIR / f"{key}{ext}"
    path.write_bytes(content)

    cached = session.get(CachedImage, key)
    if cached is None:
        cached = CachedImage(key=key, query=query)
        session.add(cached)
    cached.query = query
    cached.source_url = None
    cached.image_path = str(path)
    cached.found = True
    cached.fetched_at = int(time.time())
    session.flush()
    return cached
