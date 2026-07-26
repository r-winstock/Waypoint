// Leaflet helpers shared by the Day and Trips views. Kept theme-agnostic -
// popup colours come from the --wp-map-popup-* CSS custom properties via
// components.css, not from anything set here.

function wpTileLayer() {
  return L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 18,
  });
}

function wpInitMap(containerId) {
  const map = L.map(containerId, { scrollWheelZoom: false });
  wpTileLayer().addTo(map);
  return map;
}

function wpRenderDayMap(map, points, timeline) {
  map.eachLayer((l) => { if (l instanceof L.Polyline || l instanceof L.CircleMarker) map.removeLayer(l); });

  if (points.length) {
    const latlngs = points.map((p) => [p.lat, p.lon]);
    L.polyline(latlngs, { color: '#3b82f6', weight: 3, opacity: 0.7 }).addTo(map);
    map.fitBounds(latlngs, { padding: [24, 24] });
  }

  timeline.filter((e) => e.type === 'visit').forEach((v) => {
    const marker = L.circleMarker([v.lat, v.lon], { radius: 7, color: '#10b981', fillColor: '#10b981', fillOpacity: 0.9, weight: 2 }).addTo(map);
    marker.bindPopup(`<b>${v.place_name || 'Unnamed place'}</b><br>${v.city || ''}`);
  });

  if (!points.length) map.setView([51.5, -0.1], 6);
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
