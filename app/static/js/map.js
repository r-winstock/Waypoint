// Leaflet helpers shared by the Day and Trips views. Kept theme-agnostic -
// popup/route colours come from the --wp-* CSS custom properties via
// components.css and the theme files, not from anything hardcoded here.
// CATEGORY_ICONS is defined in app.js - safe to reference here because these
// functions only ever run later, at user-interaction time, by which point
// app.js has already loaded (script tags execute top-to-bottom, but a
// function's body only runs when called).

// noWrap stops the tile layer repeating the world horizontally when zoomed
// out far enough to see it all at once (a wide, short map container - the
// Trips overview and World choropleth both fit-bounds to widely scattered
// points/the whole world - hits this easily) - confirmed live: two Africas,
// two South Americas side by side. Paired with maxBounds on the map itself
// in wpInitMap so panning can't scroll into the (now blank) repeated area
// either.
// bounds (a GridLayer option, distinct from the map-level maxBounds tried
// and reverted for the World map - see wpInitMap's own comment) stops
// Leaflet's tile prefetch (keepBuffer, default 2 rows/columns beyond the
// viewport) from ever requesting a tile outside the real world at all -
// confirmed live this was the actual cause of a "You requested an invalid
// tile" 400 from OSM's own server at low zoom (the World map sits at
// zoom 1, a 2x2 tile grid, and the buffer prefetch reached for a
// nonexistent 3rd row). Harmless to the map itself (Leaflet just leaves
// that one tile blank) but it's a real failed request, so it correctly
// tripped the failed-external-resource banner - worth not generating in
// the first place rather than just tolerating the false alarm.
const WP_WORLD_BOUNDS = [[-90, -180], [90, 180]];

const WP_BASE_LAYERS = {
  Streets: () => L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
    noWrap: true,
    bounds: WP_WORLD_BOUNDS,
  }),
  Satellite: () => L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
    noWrap: true,
    bounds: WP_WORLD_BOUNDS,
  }),
  Terrain: () => L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap',
    maxZoom: 17,
    noWrap: true,
    bounds: WP_WORLD_BOUNDS,
  }),
};


// The public demo server - self-hosting on rowan was tried and abandoned
// (full-Europe osrm-extract needs more RAM than rowan can safely spare
// alongside its other services, twice OOM-killing things, once system-
// wide). The public server's own real limits, confirmed live by binary
// search since neither is documented anywhere: Match caps at 10
// coordinates per request, Route at 500 - see OSRM_MATCH_CHUNK_SIZE below.
const OSRM_BASE_URL = 'https://router.project-osrm.org';

// Road-network profiles the public OSRM demo router can snap a route to -
// only for modes that actually travel on a road network it knows about.
// Everything else (rail, water, air) has no equivalent routing service, so
// those draw a straight line between the two visits instead.
const OSRM_PROFILES = { driving: 'driving', taxi: 'driving', bus: 'driving', walking: 'foot', cycling: 'bike' };

function wpModeColor(mode) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--wp-mode-${mode}`).trim() || '#94a3b8';
}

function wpCategoryEmoji(category) {
  if (typeof CATEGORY_ICONS !== 'undefined' && CATEGORY_ICONS[category]) return CATEGORY_ICONS[category];
  return '\u{1F4CD}';
}

function wpCategoryDivIcon(category) {
  return L.divIcon({
    className: 'wp-map-marker',
    html: `<div class="wp-map-marker-badge">${wpCategoryEmoji(category)}</div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    popupAnchor: [0, -9],
  });
}

// A plain coloured line reads fine on a contrasting basemap but disappears
// against roads/backgrounds close to it in hue (e.g. Voyage's driving-mode
// brown against its own parchment map tiles and accent colour) - a wider
// white casing underneath, standard road-map cartography, guarantees
// contrast regardless of the line colour or the theme/tile underneath.
// Weight/opacity bumped up from the first attempt at this (3+4, 0.9) -
// confirmed live that was still too thin/faint to read clearly against busy
// satellite imagery.
function wpCasedPolyline(latlngs, color, opts = {}) {
  const group = L.layerGroup();
  L.polyline(latlngs, { color: '#ffffff', weight: (opts.weight || 4) + 6, opacity: 1, dashArray: opts.dashArray }).addTo(group);
  L.polyline(latlngs, { color, weight: opts.weight || 4, opacity: opts.opacity ?? 0.95, dashArray: opts.dashArray }).addTo(group);
  return group;
}

// Every marker/line/group this module adds is tracked here per-map so a
// re-render can clear exactly those, regardless of layer type (Marker,
// Polyline, LayerGroup) - simpler and less fragile than instanceof-filtering
// map.eachLayer(), which breaks every time a new layer type gets added and
// must also never touch the base tile layer or the layer-switcher control.
function wpClearLayers(map) {
  (map._wpLayers || []).forEach((l) => map.removeLayer(l));
  map._wpLayers = [];
}
function wpAddLayer(map, layer) {
  layer.addTo(map);
  (map._wpLayers ||= []).push(layer);
  return layer;
}

function wpInitMap(containerId, opts = {}) {
  // Guards against a real recurring shape: several detail views tear down
  // and recreate their own map container via x-if (see closeAllDetails'
  // own comment on this), so a re-render triggered while a *different*
  // view's detail is open (e.g. correcting a place from within Trip
  // detail, which refreshes the Trips tab's own overview data/map in the
  // background) can fire against a container that isn't in the DOM at all
  // right now. L.map() throws "Map container not found" in that case -
  // returning null instead lets the caller's own render function bail
  // quietly (they all already check `if (!this.trips.map) ...` etc.,
  // never assuming wpInitMap succeeded before that point regardless).
  if (!document.getElementById(containerId)) return null;
  // maxBounds was tried alongside noWrap to fix the repeated-world tiles,
  // but it fought panning/zooming near the antimeridian hard enough that
  // New Zealand (~175°E) became unreachable, confirmed live. noWrap on the
  // tile layers is what actually stops the repeat (blank space beyond the
  // real map instead of duplicate tiles) - maxBounds was only ever a nice-
  // to-have on top of that, not required, so dropped rather than keep
  // tuning it against this edge case.
  //
  // minZoom defaults to 2 (fine for the Day/Trips/City/Country pin maps,
  // which only ever fit a small cluster of points) but the World choropleth
  // needs to override it lower - confirmed live that minZoom:2 was the
  // actual reason its default view could never show enough vertical range
  // to include New Zealand or the southern half of Australia, regardless of
  // what fitBounds/setView was asked for: the map was refusing to zoom out
  // past 2 no matter what.
  const map = L.map(containerId, { scrollWheelZoom: false, minZoom: opts.minZoom ?? 2 });
  const layers = Object.fromEntries(Object.entries(WP_BASE_LAYERS).map(([name, make]) => [name, make()]));
  layers.Streets.addTo(map);
  L.control.layers(layers).addTo(map);
  wpAddFullscreenControl(map, containerId);
  return map;
}

// A single shared control so every map view (Day/Trip/City/Country/Place/
// World) gets "view larger map" for free, rather than each view wiring its
// own button - the container element itself is what goes fullscreen (the
// Fullscreen API renders it in the browser's top layer at full viewport
// size regardless of its normal in-card height), so no separate overlay/
// modal markup is needed.
function wpAddFullscreenControl(map, containerId) {
  const control = L.control({ position: 'topright' });
  control.onAdd = function () {
    const el = L.DomUtil.create('div', 'leaflet-bar wp-fullscreen-control');
    const link = L.DomUtil.create('a', '', el);
    link.href = '#';
    link.title = 'View larger map';
    link.innerHTML = '⛶';
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.on(link, 'click', L.DomEvent.stop).on(link, 'click', () => {
      const target = document.getElementById(containerId);
      if (!target) return;
      if (document.fullscreenElement === target) {
        document.exitFullscreen?.();
      } else {
        target.requestFullscreen?.();
      }
    });
    return el;
  };
  control.addTo(map);

  const containerEl = document.getElementById(containerId);
  containerEl?.addEventListener('fullscreenchange', () => {
    // The map div's on-screen size just changed (card-sized <-> viewport-
    // sized); Leaflet caches tile positions against the size it last knew
    // about, so without this it renders grey/blank past the old bounds.
    setTimeout(() => map.invalidateSize(), 50);
  });
}

async function wpFetchRoute(mode, fromLat, fromLon, toLat, toLon) {
  const profile = OSRM_PROFILES[mode];
  if (!profile) return null;
  try {
    const url = `${OSRM_BASE_URL}/route/v1/${profile}/${fromLon},${fromLat};${toLon},${toLat}?overview=full&geometries=geojson`;
    const res = await fetch(url);
    const data = await res.json();
    if (data.code !== 'Ok' || !data.routes || !data.routes.length) return null;
    return data.routes[0].geometry.coordinates.map(([lon, lat]) => [lat, lon]);
  } catch (e) {
    return null;
  }
}

// OSRM's Match service (distinct from Route, used elsewhere here) is built
// exactly for a real recorded trace, however dense or noisy: it snaps the
// whole thing onto the most likely road path, discarding/downweighting
// implausible pings along the way. Confirmed live this matters even for
// dense live-tracking data, not just sparse historical replays - a day
// recorded with OpenTracks (dense pings, but noticeably less accurate than
// OwnTracks') connected as plain straight lines between consecutive points
// zigzagged into visible spikes well off any real road. 10 is the public
// demo server's real Match limit (undocumented, confirmed live by binary
// search: 10 succeeds, 11 fails "TooBig") - a dense day's segment can
// easily need dozens of chunks at this size, each overlapping its
// neighbour by one point so the stitched-together result has no visible
// seam at the join.
const OSRM_MATCH_CHUNK_SIZE = 10;

async function wpFetchOneMatch(profile, latlngs) {
  try {
    const coords = latlngs.map(([lat, lon]) => `${lon},${lat}`).join(';');
    const url = `${OSRM_BASE_URL}/match/v1/${profile}/${coords}?overview=full&geometries=geojson`;
    const res = await fetch(url);
    const data = await res.json();
    if (data.code !== 'Ok' || !data.matchings || !data.matchings.length) return null;
    return data.matchings.flatMap((m) => m.geometry.coordinates.map(([lon, lat]) => [lat, lon]));
  } catch (e) {
    return null;
  }
}

async function wpFetchMatchedRoute(mode, latlngs) {
  const profile = OSRM_PROFILES[mode];
  if (!profile || latlngs.length < 2) return null;
  if (latlngs.length <= OSRM_MATCH_CHUNK_SIZE) return wpFetchOneMatch(profile, latlngs);

  const chunks = [];
  for (let i = 0; i < latlngs.length - 1; i += OSRM_MATCH_CHUNK_SIZE - 1) {
    chunks.push(latlngs.slice(i, i + OSRM_MATCH_CHUNK_SIZE));
  }
  const results = await Promise.all(chunks.map((c) => wpFetchOneMatch(profile, c)));
  if (results.some((r) => !r)) return null; // a failed chunk undermines the whole stitched line
  return results.flat();
}

// train/subway/tram have no OSRM equivalent (no free public router does
// timetabled/rail routing) - these are snapped server-side instead, against
// OSM railway data via Overpass, and cached per-segment since that query is
// heavier and more rate-limited than a per-render OSRM call. See
// app/routing.py and GET /api/routing/segment/{id}.
const RAIL_MODES = new Set(['train', 'subway', 'tram']);

async function wpFetchRailRoute(segmentId, fromLat, fromLon, toLat, toLon) {
  try {
    const params = new URLSearchParams({ from_lat: fromLat, from_lon: fromLon, to_lat: toLat, to_lon: toLon });
    const res = await fetch(`/api/routing/segment/${segmentId}?${params}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.points;
  } catch (e) {
    return null;
  }
}

// Raw OwnTracks points already give a real GPS-traced path - only draws a
// synthesized route for the stretch between two visits when no raw points
// cover that time range (i.e. imported history, which has visits/segments
// but no underlying GPS log at all).
function wpHasRawCoverage(points, startTs, endTs) {
  return points.some((p) => p.tst >= startTs && p.tst <= endTs);
}

// A gap this long between two consecutive raw points within an otherwise-
// covered segment is a genuine dropout (phone backgrounded, signal lost),
// not just normal reporting cadence - confirmed live: a 12-minute gap with
// perfectly ordinary speed either side (so no speed-based check catches
// it - distance and time both scale together) still isn't real recorded
// coverage for that stretch, and was being drawn as part of the solid
// "Recorded GPS path" line - a straight jump across open country - when it
// should read as the dashed "Estimated route (no GPS log for that
// stretch)" the legend already promises for exactly this case.
const RAW_GAP_MAX_S = 180;

// Splits a raw-point leg into contiguous runs wherever the gap between two
// consecutive points exceeds RAW_GAP_MAX_S - each run gets its own solid
// line, and the gaps between runs get a dashed connector (see
// wpRenderDayMap), rather than one solid line blindly connecting every
// point regardless of how long a real dropout sits between two of them.
function wpSplitByGap(legPoints) {
  const runs = [[legPoints[0]]];
  for (let i = 1; i < legPoints.length; i++) {
    if (legPoints[i].tst - legPoints[i - 1].tst > RAW_GAP_MAX_S) runs.push([]);
    runs[runs.length - 1].push(legPoints[i]);
  }
  return runs;
}

function wpHaversineM([lat1, lon1], [lat2, lon2]) {
  const r = 6371000;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

// Cumulative-distance-based sub-line, not index-based - the points aren't
// necessarily evenly spaced, so slicing by array position alone would give
// each segment a disproportionate share of a bendy path.
function wpSlicePolyline(latlngs, startFrac, endFrac) {
  if (latlngs.length < 2) return latlngs;
  const cum = [0];
  for (let i = 1; i < latlngs.length; i++) cum.push(cum[i - 1] + wpHaversineM(latlngs[i - 1], latlngs[i]));
  const total = cum[cum.length - 1];
  if (total === 0) return latlngs;

  const pointAtDist = (dist) => {
    for (let i = 1; i < cum.length; i++) {
      if (cum[i] >= dist) {
        const t = cum[i] > cum[i - 1] ? (dist - cum[i - 1]) / (cum[i] - cum[i - 1]) : 0;
        const [lat1, lon1] = latlngs[i - 1];
        const [lat2, lon2] = latlngs[i];
        return [lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t];
      }
    }
    return latlngs[latlngs.length - 1];
  };

  const startDist = startFrac * total;
  const endDist = endFrac * total;
  const middle = latlngs.filter((_, i) => cum[i] > startDist && cum[i] < endDist);
  return [pointAtDist(startDist), ...middle, pointAtDist(endDist)];
}

let _wpDayMapRenderToken = 0;

async function wpRenderDayMap(map, points, timeline, contextVisits = {}, photos = [], onPhotoClick = () => {}) {
  // Route fetches below are async and per-segment - if the day changes again
  // before they resolve, a stale route from the previous render must not get
  // drawn onto the new one.
  const renderToken = ++_wpDayMapRenderToken;

  wpClearLayers(map);

  const bounds = [];
  const visits = timeline.filter((e) => e.type === 'visit');
  const segments = timeline.filter((e) => e.type === 'segment');

  // Raw GPS points are only ever connected into a line for the time range
  // of an actual TripSegment (real recorded movement) - never across a
  // visit's own dwell period. Normal GPS drift while genuinely stationary
  // otherwise draws what looks exactly like a real journey on the map, even
  // on a day with a single visit and zero segments - confirmed live, and
  // actively misleading since it doesn't correspond to anything in the
  // visit/segment list below it.
  //
  // Colour is the segment's own mode colour, not a fixed blue - a hardcoded
  // colour here ignored the theme entirely and, worse, was the exact same
  // hue as open water on both the standard and satellite tile sets, so a
  // recorded boat/ferry crossing (which is almost always raw-GPS-covered,
  // unlike a synthesised inter-visit leg) became invisible against the lake/
  // sea it crossed. Recorded-vs-estimated is now conveyed by line style
  // (solid here, dashed below) rather than colour, so colour is free to
  // just mean "mode" consistently everywhere on the map.
  // Flanking visits resolved for every segment up front (not just ones
  // lacking raw coverage) - a manual snap_rail override on a raw-covered
  // segment still needs an endpoint pair to route between, the same as the
  // no-raw-coverage case below already does.
  const flankingVisits = new Map();
  for (let i = 0; i < timeline.length; i++) {
    const entry = timeline[i];
    if (entry.type !== 'segment') continue;
    const prevVisit = [...timeline.slice(0, i)].reverse().find((e) => e.type === 'visit') || contextVisits.before;
    const nextVisit = timeline.slice(i + 1).find((e) => e.type === 'visit') || contextVisits.after;
    if (prevVisit && nextVisit) flankingVisits.set(entry, { prevVisit, nextVisit });
  }

  const toSnapRoad = [];
  const toSnapRailFromRaw = [];
  for (const seg of segments) {
    const legPoints = points.filter((p) => p.tst >= seg.start_ts && p.tst <= seg.end_ts);
    if (legPoints.length < 2) continue;
    const color = wpModeColor(seg.mode);

    // A segment with raw coverage isn't necessarily *continuously*
    // covered - split on any internal gap long enough to be a real
    // dropout (see RAW_GAP_MAX_S), draw each real run solid, and bridge
    // the gaps between runs with the same dashed "estimated" styling a
    // segment with no raw coverage at all already gets below, rather than
    // one solid line blindly connecting every point regardless of how
    // long a dropout sits between two of them.
    const runs = wpSplitByGap(legPoints);
    const runLines = [];
    for (let i = 0; i < runs.length; i++) {
      const run = runs[i];
      const runLatlngs = run.map((p) => [p.lat, p.lon]);
      bounds.push(...runLatlngs);
      if (runLatlngs.length >= 2) runLines.push(wpAddLayer(map, wpCasedPolyline(runLatlngs, color, { opacity: 0.9 })));
      if (i < runs.length - 1) {
        const nextRun = runs[i + 1];
        const bridge = [[run[run.length - 1].lat, run[run.length - 1].lon], [nextRun[0].lat, nextRun[0].lon]];
        // Tracked in the same array as the solid runs - a successful snap
        // upgrade below removes every line drawn for this segment, bridge
        // included, replacing them all with the one new routed line.
        runLines.push(wpAddLayer(map, wpCasedPolyline(bridge, color, { opacity: 0.7, dashArray: '6 6' })));
      }
    }

    // render_mode: "raw" skips any snap attempt outright (an honest
    // recorded trace, e.g. a genuinely off-road hike a road/rail snap
    // would otherwise wrongly straighten). "snap_rail" forces an Overpass
    // rail-route attempt between this segment's own flanking visits, even
    // for a mode that wouldn't normally get one - for correcting an "auto"
    // guess that snapped to the wrong network (or didn't snap at all).
    // "snap_road"/"auto" (default) both attempt a Match-based road snap
    // for any mode with a road profile - no longer gated to sparse paths
    // only (see wpFetchMatchedRoute's own comment: confirmed live that
    // dense-but-noisy tracker data needs this too, not just sparse
    // historical replays).
    const renderMode = seg.render_mode || 'auto';
    if (renderMode === 'raw') continue;
    if (renderMode === 'snap_rail') {
      const flanking = flankingVisits.get(seg);
      if (flanking) toSnapRailFromRaw.push({ entry: seg, ...flanking, color, lines: runLines });
      continue;
    }
    if (OSRM_PROFILES[seg.mode]) {
      const latlngs = legPoints.map((p) => [p.lat, p.lon]);
      toSnapRoad.push({ mode: seg.mode, latlngs, color, lines: runLines });
    }
  }

  visits.forEach((v) => {
    const marker = L.marker([v.lat, v.lon], { icon: wpCategoryDivIcon(v.category) });
    marker.bindPopup(`<b>${v.place_name || 'Unnamed place'}</b><br>${v.city || ''}`);
    wpAddLayer(map, marker);
    bounds.push([v.lat, v.lon]);
  });

  wpAddPhotoMarkers(map, photos, bounds, onPhotoClick);

  // Route lines between consecutive visits, for whichever segments aren't
  // already covered by the raw-point polyline above. Straight dashed lines
  // are drawn synchronously for all of them right away, so the map is fully
  // visible and correctly framed immediately - a routed/rail-snapped
  // upgrade is fetched separately afterwards, per segment, without blocking
  // on it. Confirmed live: an uncached train segment's rail lookup can take
  // 10+ seconds via Overpass, and with several such segments on one day the
  // old code (which awaited each fetch before finishing the render at all)
  // left the map showing a stale/wrong view for 20-30 seconds.
  const segmentEntries = [];
  for (const entry of segments) {
    if (wpHasRawCoverage(points, entry.start_ts, entry.end_ts)) continue;
    // A segment that starts before this day's first visit, or ends after
    // its last one (an overnight arrival/departure - see day.py's
    // context_visits), has no matching Visit in today's own timeline at
    // all. flankingVisits already falls back to contextVisits for that
    // case, so this only drops a segment with genuinely no visit on
    // either side to draw to at all (confirmed live: an overnight flight
    // simply didn't appear before this fallback existed).
    const flanking = flankingVisits.get(entry);
    if (!flanking) continue;
    segmentEntries.push({ entry, ...flanking });
  }

  // Two (or more) segments with no Visit between them - e.g. a train
  // immediately followed by a taxi, with no captured stop in between -
  // resolve to the exact same prevVisit/nextVisit pair, since that lookup
  // only ever finds the nearest visit either side. Drawing each one across
  // the *full* flanking-visit distance made a real 2.4-mile taxi hop's line
  // fully overlap the 48-mile train leg right before it, confirmed live.
  // Grouping them and splitting the shared straight-line span by each
  // segment's own reported distance is a far more honest picture, even
  // though the actual split point is still a guess.
  const groups = [];
  for (const se of segmentEntries) {
    const last = groups[groups.length - 1];
    if (last && last[0].prevVisit === se.prevVisit && last[0].nextVisit === se.nextVisit) last.push(se);
    else groups.push([se]);
  }

  const toUpgrade = [];
  for (const group of groups) {
    const { prevVisit, nextVisit } = group[0];
    const fullStraight = [[prevVisit.lat, prevVisit.lon], [nextVisit.lat, nextVisit.lon]];
    const totalDist = group.reduce((sum, se) => sum + (se.entry.distance_m || 0), 0) || 1;
    let cumFrac = 0;
    for (const se of group) {
      const color = wpModeColor(se.entry.mode);
      const frac = (se.entry.distance_m || 0) / totalDist;
      const startFrac = cumFrac;
      const endFrac = group.length > 1 ? cumFrac + frac : 1;
      cumFrac = endFrac;
      const straightSlice = group.length > 1 ? wpSlicePolyline(fullStraight, startFrac, endFrac) : fullStraight;
      const line = wpAddLayer(map, wpCasedPolyline(straightSlice, color, { opacity: 0.7, dashArray: '6 6' }));
      // Only an unambiguous (ungrouped) segment gets a real routing attempt -
      // a split segment's "endpoint" is itself a guess, so a routed path to/
      // from a made-up point isn't any more accurate than the straight line.
      // render_mode "raw" skips the attempt too - an explicit "just show me
      // the straight-line estimate, don't guess further" override.
      if (group.length === 1 && (se.entry.render_mode || 'auto') !== 'raw') {
        toUpgrade.push({ entry: se.entry, prevVisit, nextVisit, color, line });
      }
    }
  }

  // animate:false throughout this file's fitBounds/setView calls - confirmed
  // live that an animated fitBounds can silently fail to actually reach its
  // target zoom/center in this environment (the map settles back at an
  // earlier, wrong view instead), the same class of issue previously found
  // with maxBounds fighting an animated setView near the antimeridian.
  if (bounds.length) map.fitBounds(bounds, { padding: [24, 24], animate: false });
  else map.setView([51.5, -0.1], 6, { animate: false });

  // Fired concurrently, not one-at-a-time - the map's already fully drawn
  // at this point, so there's no render-order dependency between them, and
  // both Overpass (rail) and OSRM (road) calls are still self-throttled
  // server-side/rate-limit-conscious regardless of how many arrive together.
  toUpgrade.forEach(async ({ entry, prevVisit, nextVisit, color, line }) => {
    const renderMode = entry.render_mode || 'auto';
    const useRail = renderMode === 'snap_rail' || (renderMode !== 'snap_road' && RAIL_MODES.has(entry.mode));
    const routed = useRail
      ? await wpFetchRailRoute(entry.id, prevVisit.lat, prevVisit.lon, nextVisit.lat, nextVisit.lon)
      : await wpFetchRoute(entry.mode, prevVisit.lat, prevVisit.lon, nextVisit.lat, nextVisit.lon);
    if (renderToken !== _wpDayMapRenderToken) return; // superseded by a newer render
    if (routed) {
      map.removeLayer(line);
      map._wpLayers = (map._wpLayers || []).filter((l) => l !== line);
      // Still dashed, even snapped to real roads - it's a router's guess at
      // the path taken, not something GPS actually recorded, and the dash
      // is the only remaining signal for that now colour is mode-only.
      wpAddLayer(map, wpCasedPolyline(routed, color, { dashArray: '4 8' }));
    } else if (useRail) {
      // No connected rail line found (confirmed live: a genuine
      // international journey - Amsterdam to London St Pancras - where
      // Overpass has no way to bridge two national rail networks across
      // open water). The mode-coloured straight-line fallback every other
      // segment gets is a reasonable stand-in for a short local hop, but
      // for a route this long it reads as a flight path, not a train.
      // Dropping the line outright (tried first) swung too far the other
      // way - confirmed live, Richard's own reaction was "the train journey
      // is not visible AT ALL", losing the one thing that mattered (these
      // two real places ARE connected by a real, if geometrically unknown,
      // journey). Restyled instead: thin, muted neutral grey, sparse dots -
      // deliberately unlike both a confident route guess (this app's own
      // per-mode colour + '6 6'/'4 8' dashing) and how "flying" itself
      // renders, so it reads as "connected, path unrecorded" rather than
      // either "here's the path" or "nothing happened here".
      map.removeLayer(line);
      map._wpLayers = (map._wpLayers || []).filter((l) => l !== line);
      const dimColor = getComputedStyle(document.documentElement).getPropertyValue('--wp-fg-dim').trim() || '#94a3b8';
      const straight = [[prevVisit.lat, prevVisit.lon], [nextVisit.lat, nextVisit.lon]];
      wpAddLayer(map, L.polyline(straight, { color: dimColor, weight: 2, opacity: 0.6, dashArray: '1 9' }));
    }
  });

  // Same non-blocking upgrade pattern, for recorded raw-point paths - stays
  // solid, not dashed, even once snapped: it's still built from real
  // recorded waypoints, just following actual roads between them now
  // instead of a straight line jumping across whatever's between two
  // fixes (was previously only attempted for sparse historical replays;
  // wpFetchMatchedRoute's own comment covers why dense live-tracking data
  // needs this too).
  toSnapRoad.forEach(async ({ mode, latlngs, color, lines }) => {
    const routed = await wpFetchMatchedRoute(mode, latlngs);
    if (renderToken !== _wpDayMapRenderToken) return;
    if (routed) {
      lines.forEach((l) => map.removeLayer(l));
      map._wpLayers = (map._wpLayers || []).filter((l) => !lines.includes(l));
      wpAddLayer(map, wpCasedPolyline(routed, color, { opacity: 0.9 }));
    }
  });

  // A manual snap_rail override on a segment that DOES have raw coverage -
  // routes between its flanking visits (rail routing is endpoint-based, see
  // app/routing.py, not built from the recorded points themselves), so the
  // result is a router's guess like the no-raw-coverage case above, hence
  // dashed rather than solid despite replacing an originally-solid line.
  toSnapRailFromRaw.forEach(async ({ entry, prevVisit, nextVisit, color, lines }) => {
    const routed = await wpFetchRailRoute(entry.id, prevVisit.lat, prevVisit.lon, nextVisit.lat, nextVisit.lon);
    if (renderToken !== _wpDayMapRenderToken) return;
    if (routed) {
      lines.forEach((l) => map.removeLayer(l));
      map._wpLayers = (map._wpLayers || []).filter((l) => !lines.includes(l));
      wpAddLayer(map, wpCasedPolyline(routed, color, { dashArray: '4 8' }));
    }
  });
}

// Plots PhotoPrism photos directly on the map (like PhotoPrism's own Places
// view), rather than only as a strip below it - photos taken at (near
// enough) the same spot are grouped into one marker instead of stacking
// identical pins on top of each other (confirmed live: several photos taken
// at one house otherwise rendered as unclickable overlapping dots). 4
// decimal places (~11m) is a coarse-but-reasonable "same spot" threshold -
// good enough to collapse a house/venue's own photos together without
// merging genuinely distinct nearby places.
const WP_PHOTO_GROUP_PRECISION = 4;

function wpGroupPhotosByLocation(photos) {
  const groups = new Map();
  (photos || []).forEach((p) => {
    if (p.lat == null || p.lon == null) return; // no GPS on this photo - gallery-strip only, can't be plotted
    const key = `${p.lat.toFixed(WP_PHOTO_GROUP_PRECISION)},${p.lon.toFixed(WP_PHOTO_GROUP_PRECISION)}`;
    if (!groups.has(key)) groups.set(key, { lat: p.lat, lon: p.lon, photos: [] });
    groups.get(key).photos.push(p);
  });
  return [...groups.values()];
}

function wpPhotoDivIcon(coverPhoto, count) {
  const badge = count > 1 ? `<div class="wp-photo-marker-count">${count}</div>` : '';
  return L.divIcon({
    className: 'wp-photo-marker',
    html: `<div class="wp-photo-marker-thumb" style="background-image: url('${coverPhoto.thumb_url}')"></div>${badge}`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -20],
  });
}

// Shared by wpRenderDayMap (Day + Trip detail, both call it) - kept as its
// own function since a Place detail view's map (if one exists later) would
// want the same grouping/marker logic without the rest of wpRenderDayMap's
// route/segment handling.
//
// onPhotoClick opens Waypoint's own in-app photo viewer (app.js's
// openPhotoViewer) rather than this module reaching for window.open
// itself - map.js has no Alpine/PhotoPrism-URL knowledge of its own, so the
// click behaviour is threaded in as a callback rather than hardcoded here.
function wpAddPhotoMarkers(map, photos, bounds, onPhotoClick) {
  wpGroupPhotosByLocation(photos).forEach((g) => {
    const cover = g.photos[0];
    const marker = L.marker([g.lat, g.lon], { icon: wpPhotoDivIcon(cover, g.photos.length) });
    marker.on('click', () => onPhotoClick(cover));
    wpAddLayer(map, marker);
    bounds.push([g.lat, g.lon]);
  });
}

function wpRenderPins(map, pins) {
  wpClearLayers(map);
  const latlngs = [];
  pins.forEach((p) => {
    if (p.lat == null || p.lon == null) return;
    latlngs.push([p.lat, p.lon]);
    const marker = L.marker([p.lat, p.lon], { icon: wpCategoryDivIcon(p.category) });
    if (p.label) marker.bindPopup(p.label);
    wpAddLayer(map, marker);
  });
  if (latlngs.length) map.fitBounds(latlngs, { padding: [24, 24], animate: false });
  else map.setView([51.5, -0.1], 6, { animate: false });
}

// World view's choropleth. countries.geo.json (bundled, ~250KB simplified
// geometry) keys each feature's "id" by ISO 3166-1 alpha-3, but the app's
// own country_code fields are alpha-2 (what Nominatim/Google's addresses
// give) - WP_ISO_A3_TO_A2 (world-codes.js) bridges the two rather than
// re-deriving alpha-3 everywhere else in the app just for this one map.
let _wpWorldGeoJsonCache = null;
async function _wpLoadWorldGeoJson() {
  if (_wpWorldGeoJsonCache) return _wpWorldGeoJsonCache;
  const res = await fetch('/static/data/countries.geo.json');
  _wpWorldGeoJsonCache = await res.json();
  return _wpWorldGeoJsonCache;
}

async function wpRenderWorldMap(map, visitedCodes) {
  wpClearLayers(map);
  const geojson = await _wpLoadWorldGeoJson();
  const visitedColor = getComputedStyle(document.documentElement).getPropertyValue('--wp-accent').trim() || '#3b82f6';
  const unvisitedColor = getComputedStyle(document.documentElement).getPropertyValue('--wp-border').trim() || '#cbd5e1';

  const layer = L.geoJSON(geojson, {
    style: (feature) => {
      const a2 = WP_ISO_A3_TO_A2[feature.id];
      const visited = a2 && visitedCodes.has(a2);
      return {
        fillColor: visited ? visitedColor : unvisitedColor,
        fillOpacity: visited ? 0.65 : 0.25,
        color: getComputedStyle(document.documentElement).getPropertyValue('--wp-border').trim() || '#94a3b8',
        weight: 1,
      };
    },
    onEachFeature: (feature, l) => {
      l.bindPopup(feature.properties.name);
    },
  });
  wpAddLayer(map, layer);
  // Explicit setView, not fitBounds - fitBounds proved unreliable for this
  // map across several attempts (fitting the real data's bounds, at -85°/
  // +84°, cut off Europe/Russia/Canada; a fixed [-58,78] bounds request
  // still only rendered -26° to +64° - the map's minZoom:2 was silently
  // preventing it from ever zooming out far enough regardless of what was
  // requested, confirmed live New Zealand and half of Australia stayed
  // clipped either way). wpInitMap now creates this specific map with
  // minZoom:0, and this is a directly-tested zoom/center that comfortably
  // covers pole-to-pole -70°/+76° in this container's actual aspect ratio -
  // confirmed live to include New Zealand and all of Australia.
  map.setView([10, 0], 1, { animate: false });
}
