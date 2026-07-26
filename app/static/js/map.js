// Leaflet helpers shared by the Day and Trips views. Kept theme-agnostic -
// popup/route colours come from the --wp-* CSS custom properties via
// components.css and the theme files, not from anything hardcoded here.
// CATEGORY_ICONS is defined in app.js - safe to reference here because these
// functions only ever run later, at user-interaction time, by which point
// app.js has already loaded (script tags execute top-to-bottom, but a
// function's body only runs when called).

const WP_BASE_LAYERS = {
  Streets: () => L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  }),
  Satellite: () => L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
  }),
  Terrain: () => L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap',
    maxZoom: 17,
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
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -13],
  });
}

// A plain coloured line reads fine on a contrasting basemap but disappears
// against roads/backgrounds close to it in hue (e.g. Voyage's driving-mode
// brown against its own parchment map tiles and accent colour) - a wider
// white casing underneath, standard road-map cartography, guarantees
// contrast regardless of the line colour or the theme/tile underneath.
function wpCasedPolyline(latlngs, color, opts = {}) {
  const group = L.layerGroup();
  L.polyline(latlngs, { color: '#ffffff', weight: (opts.weight || 3) + 4, opacity: 0.9, dashArray: opts.dashArray }).addTo(group);
  L.polyline(latlngs, { color, weight: opts.weight || 3, opacity: opts.opacity ?? 0.95, dashArray: opts.dashArray }).addTo(group);
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

function wpInitMap(containerId) {
  const map = L.map(containerId, { scrollWheelZoom: false });
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

// Raw OwnTracks points already give a real GPS-traced path - only draws a
// synthesized route for the stretch between two visits when no raw points
// cover that time range (i.e. imported history, which has visits/segments
// but no underlying GPS log at all).
function wpHasRawCoverage(points, startTs, endTs) {
  return points.some((p) => p.tst >= startTs && p.tst <= endTs);
}

let _wpDayMapRenderToken = 0;

async function wpRenderDayMap(map, points, timeline) {
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
  for (const seg of segments) {
    const legPoints = points.filter((p) => p.tst >= seg.start_ts && p.tst <= seg.end_ts);
    if (legPoints.length < 2) continue;
    const latlngs = legPoints.map((p) => [p.lat, p.lon]);
    wpAddLayer(map, wpCasedPolyline(latlngs, '#3b82f6', { opacity: 0.75 }));
    bounds.push(...latlngs);
  }

  visits.forEach((v) => {
    const marker = L.marker([v.lat, v.lon], { icon: wpCategoryDivIcon(v.category) });
    marker.bindPopup(`<b>${v.place_name || 'Unnamed place'}</b><br>${v.city || ''}`);
    wpAddLayer(map, marker);
    bounds.push([v.lat, v.lon]);
  });

  // Route lines between consecutive visits, for whichever segments aren't
  // already covered by the raw-point polyline above.
  for (let i = 0; i < timeline.length; i++) {
    const entry = timeline[i];
    if (entry.type !== 'segment') continue;
    if (wpHasRawCoverage(points, entry.start_ts, entry.end_ts)) continue;

    const prevVisit = [...timeline.slice(0, i)].reverse().find((e) => e.type === 'visit');
    const nextVisit = timeline.slice(i + 1).find((e) => e.type === 'visit');
    if (!prevVisit || !nextVisit) continue;

    const color = wpModeColor(entry.mode);
    const straight = [[prevVisit.lat, prevVisit.lon], [nextVisit.lat, nextVisit.lon]];
    const line = wpAddLayer(map, wpCasedPolyline(straight, color, { opacity: 0.7, dashArray: '6 6' }));

    const routed = await wpFetchRoute(entry.mode, prevVisit.lat, prevVisit.lon, nextVisit.lat, nextVisit.lon);
    if (renderToken !== _wpDayMapRenderToken) return; // superseded by a newer render
    if (routed) {
      map.removeLayer(line);
      map._wpLayers = (map._wpLayers || []).filter((l) => l !== line);
      wpAddLayer(map, wpCasedPolyline(routed, color));
    }
  }

  if (bounds.length) map.fitBounds(bounds, { padding: [24, 24] });
  else map.setView([51.5, -0.1], 6);
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
  if (latlngs.length) map.fitBounds(latlngs, { padding: [24, 24] });
  else map.setView([51.5, -0.1], 6);
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
  map.fitBounds(layer.getBounds());
}
