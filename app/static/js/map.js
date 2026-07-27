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
const WP_BASE_LAYERS = {
  Streets: () => L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
    noWrap: true,
  }),
  Satellite: () => L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
    noWrap: true,
  }),
  Terrain: () => L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap',
    maxZoom: 17,
    noWrap: true,
  }),
};


// Road-network profiles the public OSRM demo router can snap a route to -
// only for modes that actually travel on a road network it knows about.
// Everything else (rail, water, air) has no equivalent free routing service,
// so those draw a straight line between the two visits instead.
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
  return map;
}

async function wpFetchRoute(mode, fromLat, fromLon, toLat, toLon) {
  const profile = OSRM_PROFILES[mode];
  if (!profile) return null;
  try {
    const url = `https://router.project-osrm.org/route/v1/${profile}/${fromLon},${fromLat};${toLon},${toLat}?overview=full&geometries=geojson`;
    const res = await fetch(url);
    const data = await res.json();
    if (data.code !== 'Ok' || !data.routes || !data.routes.length) return null;
    return data.routes[0].geometry.coordinates.map(([lon, lat]) => [lat, lon]);
  } catch (e) {
    return null;
  }
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

async function wpRenderDayMap(map, points, timeline, contextVisits = {}) {
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
  for (const seg of segments) {
    const legPoints = points.filter((p) => p.tst >= seg.start_ts && p.tst <= seg.end_ts);
    if (legPoints.length < 2) continue;
    const latlngs = legPoints.map((p) => [p.lat, p.lon]);
    wpAddLayer(map, wpCasedPolyline(latlngs, wpModeColor(seg.mode), { opacity: 0.9 }));
    bounds.push(...latlngs);
  }

  visits.forEach((v) => {
    const marker = L.marker([v.lat, v.lon], { icon: wpCategoryDivIcon(v.category) });
    marker.bindPopup(`<b>${v.place_name || 'Unnamed place'}</b><br>${v.city || ''}`);
    wpAddLayer(map, marker);
    bounds.push([v.lat, v.lon]);
  });

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
  for (let i = 0; i < timeline.length; i++) {
    const entry = timeline[i];
    if (entry.type !== 'segment') continue;
    if (wpHasRawCoverage(points, entry.start_ts, entry.end_ts)) continue;

    // A segment that starts before this day's first visit, or ends after
    // its last one (an overnight arrival/departure - see day.py's
    // context_visits), has no matching Visit in today's own timeline at
    // all. Falls back to the nearest visit just outside the day so the
    // segment still has somewhere to draw to, rather than being silently
    // dropped from the map entirely (confirmed live: an overnight flight
    // simply didn't appear).
    const prevVisit = [...timeline.slice(0, i)].reverse().find((e) => e.type === 'visit') || contextVisits.before;
    const nextVisit = timeline.slice(i + 1).find((e) => e.type === 'visit') || contextVisits.after;
    if (!prevVisit || !nextVisit) continue;
    segmentEntries.push({ entry, prevVisit, nextVisit });
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
      if (group.length === 1) toUpgrade.push({ entry: se.entry, prevVisit, nextVisit, color, line });
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
    const routed = RAIL_MODES.has(entry.mode)
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
    }
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
