"""Best-effort rail-line snapping for train/subway/tram segments.

OSRM (used for driving/walking/cycling - see map.js's OSRM_PROFILES) has no
free public equivalent for rail routing - transit routing needs timetable/
network data no keyless demo server exposes. This builds a small graph from
OSM railway ways near the two visits (via Overpass, the same free/keyless
tool geocoding.py already uses for nearby-place lookups) and finds the
shortest path across it - the same kind of thing OSRM does for roads, just
self-built for the one mode class no public router covers.
"""
from __future__ import annotations

import heapq
import json
import math
import time

import httpx
from sqlalchemy.orm import Session

from app.models import CachedRoute

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Waypoint/0.1 (self-hosted personal timeline; contact rwinstock@hotmail.com)"

# Overpass's own usage policy is looser than Nominatim's but still asks for
# restraint - matches geocoding.py's existing throttle for the same API.
MIN_INTERVAL_S = 1.05
_last_call = 0.0

RAIL_TAGS = {
    "train": "rail|light_rail",
    "subway": "subway|light_rail",
    "tram": "tram|light_rail",
}

# A station approach can loop away from the direct line between two visits,
# so the query box needs real margin beyond the straight-line bounding box -
# but too much margin turns this into a slow, huge Overpass response for
# little benefit. ~0.15 degrees is generous for an inter-city or metro hop
# without ballooning query size.
BBOX_MARGIN_DEG = 0.15

# A fixed margin is fine for a regional hop but wrong for a genuine long-haul
# journey: real rail routes routinely detour well outside the endpoints' own
# straight-line box (a mountain pass, a coastline, a border crossing) - a
# fixed 0.15 degrees left Amsterdam-London (346mi via Brussels and the
# Channel Tunnel, which dips south of London itself to reach Calais/
# Folkestone) with no chance of ever finding a connected route, confirmed
# live: Overpass had nothing to work with because the tunnel corridor simply
# wasn't in the box at all. Scales the margin with the endpoints' own
# straight-line distance instead, floored at BBOX_MARGIN_DEG for short hops
# and capped so a truly enormous distance (a flight misclassified as a
# train, say) doesn't turn into an unbounded Overpass query.
MARGIN_DISTANCE_FRACTION = 0.25
MAX_BBOX_MARGIN_DEG = 2.5


def _bbox_margin_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    distance_km = _haversine_m(lat1, lon1, lat2, lon2) / 1000.0
    scaled_deg = (distance_km * MARGIN_DISTANCE_FRACTION) / 111.0
    return min(max(BBOX_MARGIN_DEG, scaled_deg), MAX_BBOX_MARGIN_DEG)


# Light rail/tram networks don't span this far - past it, a "train" query
# only asks for mainline "rail" and drops "light_rail" entirely. Confirmed
# live this was the real cost driver for a long-haul box, not the box size
# alone: Amsterdam-London's widened bbox covers three cities' worth of dense
# metro/tram network (Amsterdam, Brussels, London) that a genuine 346-mile
# inter-city journey was never going to use anyway, and Overpass timed out
# trying to return all of it.
LIGHT_RAIL_MAX_KM = 50.0

# If neither visit is within this of the rail network Overpass returned,
# treat it as "no usable rail data here" rather than snapping to a distant,
# unrelated line.
MAX_SNAP_DISTANCE_M = 2000.0


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class RailFetchError(Exception):
    """Overpass itself failed or was unreachable - distinct from a query
    that succeeded and genuinely found no matching ways. Confirmed live
    that the public Overpass instance occasionally times out or serves an
    empty/malformed response under load even for a query that works fine
    moments later - conflating that with "no rail line here" would cache a
    transient failure as permanent, the same real bug already hit and fixed
    for Wikipedia/Wikimedia lookups in images.py."""


def _fetch_rail_ways(mode: str, lat1: float, lon1: float, lat2: float, lon2: float) -> list[list[tuple[float, float]]]:
    tag_filter = RAIL_TAGS.get(mode, "rail|light_rail")
    distance_km = _haversine_m(lat1, lon1, lat2, lon2) / 1000.0
    if distance_km > LIGHT_RAIL_MAX_KM:
        tag_filter = tag_filter.replace("|light_rail", "")
    margin = _bbox_margin_deg(lat1, lon1, lat2, lon2)
    min_lat, max_lat = sorted([lat1, lat2])
    min_lon, max_lon = sorted([lon1, lon2])
    south, west = min_lat - margin, min_lon - margin
    north, east = max_lat + margin, max_lon + margin
    # timeout scales with the query itself for the same reason the bbox
    # does - a long-haul box covering a real international detour returns
    # far more data than a regional hop's, and the original fixed 20s
    # Overpass-side budget (25s client-side) was tuned for that smaller
    # case.
    # Capped well below Overpass's own apparent ceiling for the [timeout:X]
    # request parameter - a couple of manual probes above ~45 got an
    # immediate 406 rather than a slow answer, suggesting the public
    # instance rejects an overly ambitious ask outright rather than just
    # taking its time (unconfirmed precisely - those probes were likely
    # also caught by rate-limiting from hitting the same endpoint
    # repeatedly - but there's no upside to testing that ceiling here).
    overpass_timeout = 20 if margin <= BBOX_MARGIN_DEG else min(45, int(20 + margin * 15))
    query = f"""
    [out:json][timeout:{overpass_timeout}];
    way["railway"~"^({tag_filter})$"]({south},{west},{north},{east});
    out geom;
    """
    _throttle()
    try:
        resp = httpx.post(
            OVERPASS_URL, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=overpass_timeout + 5.0
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise RailFetchError(str(e)) from e

    ways = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if geom and len(geom) >= 2:
            ways.append([(pt["lat"], pt["lon"]) for pt in geom if "lat" in pt and "lon" in pt])
    return ways


def _build_graph(ways: list[list[tuple[float, float]]]):
    """Node key = a rounded coordinate, not an OSM node id - Overpass's `out
    geom` gives each way's points inline without the shared node references
    that would otherwise let two ways be known to touch at a junction.
    Rounding to 6dp (~0.1m) is far tighter than real track spacing, so it
    only merges points that are genuinely the same junction."""

    def node_key(pt: tuple[float, float]) -> tuple[float, float]:
        return (round(pt[0], 6), round(pt[1], 6))

    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
    coords: dict[tuple[float, float], tuple[float, float]] = {}
    for way in ways:
        for a, b in zip(way, way[1:]):
            ka, kb = node_key(a), node_key(b)
            coords[ka], coords[kb] = a, b
            dist = _haversine_m(a[0], a[1], b[0], b[1])
            graph.setdefault(ka, []).append((kb, dist))
            graph.setdefault(kb, []).append((ka, dist))
    return graph, coords


def _nearest_node(coords: dict, lat: float, lon: float) -> tuple[tuple[float, float] | None, float]:
    best_key, best_dist = None, float("inf")
    for key, (nlat, nlon) in coords.items():
        d = _haversine_m(lat, lon, nlat, nlon)
        if d < best_dist:
            best_key, best_dist = key, d
    return best_key, best_dist


def _dijkstra(graph: dict, start, end) -> list | None:
    dist = {start: 0.0}
    prev = {}
    visited = set()
    heap = [(0.0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == end:
            break
        for neighbor, weight in graph.get(node, []):
            nd = d + weight
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                prev[neighbor] = node
                heapq.heappush(heap, (nd, neighbor))
    if end not in dist:
        return None
    path = [end]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def compute_rail_route(
    mode: str, lat1: float, lon1: float, lat2: float, lon2: float
) -> list[tuple[float, float]] | None:
    ways = _fetch_rail_ways(mode, lat1, lon1, lat2, lon2)
    if not ways:
        return None
    graph, coords = _build_graph(ways)
    if not coords:
        return None
    start_node, start_dist = _nearest_node(coords, lat1, lon1)
    end_node, end_dist = _nearest_node(coords, lat2, lon2)
    if start_node is None or end_node is None:
        return None
    if start_dist > MAX_SNAP_DISTANCE_M or end_dist > MAX_SNAP_DISTANCE_M:
        return None
    path = _dijkstra(graph, start_node, end_node)
    if not path:
        return None
    return [coords[k] for k in path]


def get_or_fetch_rail_route(
    session: Session, segment_id: int, mode: str, lat1: float, lon1: float, lat2: float, lon2: float
) -> CachedRoute:
    cached = session.get(CachedRoute, segment_id)
    if cached is not None:
        return cached

    try:
        points = compute_rail_route(mode, lat1, lon1, lat2, lon2)
    except RailFetchError:
        # Overpass itself failed - return an unsaved result so the next
        # request retries fresh rather than trusting a transient failure as
        # a permanent "no route" forever (see RailFetchError's docstring).
        return CachedRoute(segment_id=segment_id, mode=mode, points_json=None, found=False, fetched_at=int(time.time()))

    cached = CachedRoute(
        segment_id=segment_id,
        mode=mode,
        points_json=json.dumps(points) if points else None,
        found=points is not None,
        fetched_at=int(time.time()),
    )
    session.add(cached)
    session.flush()
    return cached
