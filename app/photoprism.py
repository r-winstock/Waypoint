"""PhotoPrism client - surfaces the user's own real photos (taken at a given
place and/or during a given time window) alongside the generic Wikipedia
stock photo already used for card backgrounds (see app/images.py's own
module docstring for that system, which this complements rather than
replaces - a Day/Trip/Place view's own real photos are a different, better
thing than a representative stock image where they exist, but Wikipedia
stays the fallback for Cities/Countries/category cards where "my own photos
of an entire city" is too fuzzy a match to be useful).

Every function here degrades to [] rather than raising when
PHOTOPRISM_URL/PHOTOPRISM_TOKEN aren't configured, or a request fails -
this is always a best-effort personal-photo gallery layered on top of
Waypoint's own timeline data, never a hard dependency (same convention as
app/google_places.py).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import httpx

USER_AGENT = "Waypoint/0.1 (self-hosted personal timeline; contact rwinstock@hotmail.com)"

# Self-throttled the same way Nominatim/Wikipedia/Google Places already are
# elsewhere in this app - a Trip/Day/Place detail page can trigger several
# of these in quick succession, and there's no reason to hammer even a
# self-hosted, LAN-only API harder than necessary.
MIN_INTERVAL_S = 0.1
_last_call = 0.0

# PhotoPrism's own preview token (from /api/v1/config) is needed to build a
# cookie-free thumbnail URL - it doesn't change at runtime, so it's fetched
# once per process and cached rather than re-requested on every gallery.
_preview_token: str | None = None


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _base_url() -> str | None:
    url = os.environ.get("PHOTOPRISM_URL")
    return url.rstrip("/") if url else None


def _session_token() -> str | None:
    return os.environ.get("PHOTOPRISM_TOKEN")


def _get_preview_token(base: str, session_token: str) -> str | None:
    global _preview_token
    if _preview_token is not None:
        return _preview_token
    _throttle()
    try:
        resp = httpx.get(
            f"{base}/api/v1/config",
            headers={"X-Session-ID": session_token, "User-Agent": USER_AGENT},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        _preview_token = resp.json().get("previewToken")
        return _preview_token
    except (httpx.HTTPError, ValueError):
        return None


def _thumb_url(base: str, preview_token: str, photo_hash: str, size: str = "tile_500") -> str:
    return f"{base}/api/v1/t/{photo_hash}/{preview_token}/{size}"


def _photo_page_url(base: str, photo_hash: str | None) -> str | None:
    """PhotoPrism has no stable single-photo permalink route (confirmed via
    its own frontend router - photo view is a modal opened from within a
    browse/search list, not a dedicated URL). Deep-linking into its search
    view filtered to this photo's own hash (a supported search filter, and
    already fetched for the thumbnail URL above) is the closest equivalent -
    lands on a one-result gallery the user can click straight into."""
    return f"{base}/library/photos?q=hash:{photo_hash}" if photo_hash else None


def nearby_photos(
    lat: float | None = None,
    lon: float | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    radius_km: float = 0.3,
    limit: int = 24,
) -> list[dict]:
    """Photos matching whichever of geo/time filters are given - a Place
    gallery passes only lat/lon (any date, since a place is revisited over
    years), a Day/Trip gallery passes only start_ts/end_ts (any location,
    since a day or trip can span several places). Both together would also
    work but no current caller needs it.

    end_ts is padded by a day before being sent as PhotoPrism's `before`
    filter - confirmed (via PhotoPrism's own issue tracker) that `before`
    compares a photo's full taken-at datetime against midnight on the given
    date, so an unpadded same-day boundary silently drops photos taken
    later that day. `after` has no such bug (documented as inclusive), so
    start_ts is sent as-is."""
    base, token = _base_url(), _session_token()
    if not base or not token:
        return []
    preview_token = _get_preview_token(base, token)
    if not preview_token:
        return []

    params: dict[str, float | int | str] = {"count": limit}
    if start_ts is not None:
        params["after"] = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    if end_ts is not None:
        before = datetime.fromtimestamp(end_ts, tz=timezone.utc) + timedelta(days=1)
        params["before"] = before.strftime("%Y-%m-%d")
    if lat is not None and lon is not None:
        params.update({"lat": lat, "lng": lon, "dist": radius_km})

    _throttle()
    try:
        resp = httpx.get(
            f"{base}/api/v1/photos",
            params=params,
            headers={"X-Session-ID": token, "User-Agent": USER_AGENT},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        photos = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    # Deduped by UID, not Hash - confirmed live (via the exact JSON PhotoPrism
    # returned) that a photo with two matching file variants (a Live Photo's
    # image+video pair, in this case) comes back as two rows sharing one
    # identical UID but two different Hash values. An earlier version of
    # this function deduped on Hash, which doesn't catch that case at all
    # (the two rows have genuinely different hashes) - the gallery strip's
    # Alpine x-for is keyed on uid, so the duplicate uid was still throwing
    # hard enough to blank the *entire* gallery (the same failure mode
    # found live in the World country list), even after that first,
    # wrongly-keyed dedup attempt. The map itself was never affected -
    # wpGroupPhotosByLocation groups by coordinate, not identity, so a
    # duplicate uid there just harmlessly counts as two photos in one
    # marker's badge.
    results = []
    seen_uids: set[str] = set()
    for p in photos or []:
        photo_hash = p.get("Hash")
        photo_uid = p.get("UID")
        if not photo_hash or not photo_uid or photo_uid in seen_uids:
            continue
        seen_uids.add(photo_uid)
        # PhotoPrism marshals a photo with no GPS EXIF as Lat: 0, Lng: 0
        # (Go's zero value for an unset float field), not as a null/omitted
        # field - confirmed live this plotted a UK photo with no location
        # data at (0, 0), the Gulf of Guinea off the coast of West Africa
        # ("Null Island"). A real photo at exactly (0, 0) is practically
        # impossible, so this is always treated as "no location" rather
        # than a genuine coordinate.
        p_lat, p_lon = p.get("Lat"), p.get("Lng")
        if p_lat == 0 and p_lon == 0:
            p_lat = p_lon = None
        results.append(
            {
                "uid": photo_uid,
                "taken_at": p.get("TakenAt"),
                "lat": p_lat,
                "lon": p_lon,
                "thumb_url": _thumb_url(base, preview_token, photo_hash),
                "page_url": _photo_page_url(base, photo_hash),
            }
        )
    return results
