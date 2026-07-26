// Leaflet helpers shared by the Day and Trips views. Kept theme-agnostic -
// popup/route colours come from the --wp-* CSS custom properties via
// components.css and the theme files, not from anything hardcoded here.

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

  map.eachLayer((l) => { if (l instanceof L.Polyline || l instanceof L.CircleMarker) map.removeLayer(l); });

  const bounds = [];

  if (points.length) {
    const latlngs = points.map((p) => [p.lat, p.lon]);
    L.polyline(latlngs, { color: '#3b82f6', weight: 3, opacity: 0.7 }).addTo(map);
    bounds.push(...latlngs);
  }

  const visits = timeline.filter((e) => e.type === 'visit');
  visits.forEach((v) => {
    const marker = L.circleMarker([v.lat, v.lon], { radius: 7, color: '#10b981', fillColor: '#10b981', fillOpacity: 0.9, weight: 2 }).addTo(map);
    marker.bindPopup(`<b>${v.place_name || 'Unnamed place'}</b><br>${v.city || ''}`);
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
    const line = L.polyline(straight, { color, weight: 3, opacity: 0.6, dashArray: '6 6' }).addTo(map);

    const routed = await wpFetchRoute(entry.mode, prevVisit.lat, prevVisit.lon, nextVisit.lat, nextVisit.lon);
    if (renderToken !== _wpDayMapRenderToken) return; // superseded by a newer render
    if (routed) {
      map.removeLayer(line);
      L.polyline(routed, { color, weight: 3, opacity: 0.8 }).addTo(map);
    }
  }

  if (bounds.length) map.fitBounds(bounds, { padding: [24, 24] });
  else map.setView([51.5, -0.1], 6);
}

function wpRenderPins(map, pins) {
  map.eachLayer((l) => { if (l instanceof L.CircleMarker) map.removeLayer(l); });
  const latlngs = [];
  pins.forEach((p) => {
    if (p.lat == null || p.lon == null) return;
    latlngs.push([p.lat, p.lon]);
    const marker = L.circleMarker([p.lat, p.lon], { radius: 6, color: '#8b5cf6', fillColor: '#8b5cf6', fillOpacity: 0.9, weight: 2 }).addTo(map);
    if (p.label) marker.bindPopup(p.label);
  });
  if (latlngs.length) map.fitBounds(latlngs, { padding: [24, 24] });
  else map.setView([51.5, -0.1], 6);
}
