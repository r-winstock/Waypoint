function slugify(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

function countryFlag(code) {
  if (!code || code.length !== 2) return '';
  const base = 127397; // regional indicator offset from ASCII
  return String.fromCodePoint(...[...code.toUpperCase()].map((c) => c.charCodeAt(0) + base));
}

function formatMiles(m) {
  const mi = m / 1609.344;
  if (mi < 0.1) return '0 mi';
  return `${mi.toFixed(mi < 10 ? 1 : 0)} mi`;
}

function formatDuration(seconds) {
  const s = Math.round(seconds || 0);
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (h === 0) return `${m} min`;
  if (m === 0) return `${h} hr${h > 1 ? 's' : ''}`;
  return `${h} hr${h > 1 ? 's' : ''} ${m} min`;
}

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function formatDateRange(startTs, endTs) {
  const opts = { day: 'numeric', month: 'long', year: 'numeric' };
  const start = new Date(startTs * 1000);
  const end = new Date(endTs * 1000);
  if (start.toDateString() === end.toDateString()) return start.toLocaleDateString('en-GB', opts);
  return `${start.toLocaleDateString('en-GB', { day: 'numeric', month: 'long' })} – ${end.toLocaleDateString('en-GB', opts)}`;
}

function formatRelative(ts) {
  const days = Math.floor((Date.now() / 1000 - ts) / 86400);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days} days ago`;
  if (days < 31) return `${Math.floor(days / 7)} week${Math.floor(days / 7) > 1 ? 's' : ''} ago`;
  if (days < 365) return `${Math.floor(days / 30)} month${Math.floor(days / 30) > 1 ? 's' : ''} ago`;
  return new Date(ts * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const CATEGORY_ICONS = {
  'Home': '\u{1F3E0}',
  'Work': '\u{1F4BC}',
  'Food and drink': '\u{1F374}',
  'Shopping': '\u{1F6CD}',
  'Hotels': '\u{1F6CF}',
  'Culture': '\u{1F3DB}',
  'Sports': '\u{26BD}',
  'Airports': '✈',
  'Other places': '\u{1F4CD}',
};

const CATEGORY_OPTIONS = Object.keys(CATEGORY_ICONS);

const MODE_ICONS = {
  walking: 'footprints',
  cycling: 'bike',
  driving: 'car',
  taxi: 'car-front',
  bus: 'bus',
  train: 'train-front',
  subway: 'train-front',
  tram: 'tram-front',
  ferry: 'ship',
  boating: 'sailboat',
  flying: 'plane',
};

// Visual weight of a timeline row reflects how long it actually lasted -
// sqrt-scaled so an 11-hour overnight stay doesn't push a 6-minute stop off
// the bottom of the screen, while still reading as clearly longer.
function entryLineHeight(startTs, endTs) {
  const minutes = Math.max(1, (endTs - startTs) / 60);
  return Math.min(160, Math.max(24, 16 + Math.sqrt(minutes) * 8));
}

function waypoint() {
  return {
    theme: localStorage.getItem('waypoint-theme') || 'atlas',
    themeMenuOpen: false,
    tab: 'day',

    day: { date: todayIso(), data: null, loading: false, map: null },
    trips: { data: null, loading: false, map: null, detail: null, detailLoading: false, detailMap: null },
    insights: { year: new Date().getFullYear(), month: new Date().getMonth() + 1, data: null, loading: false },
    places: { data: null, loading: false, category: null, categoryData: null },
    cities: { data: null, loading: false },
    world: { data: null, loading: false },

    placeEdit: {
      open: false, placeId: null, name: '', category: 'Other places', city: '', country: '', countryCode: '',
      alternatives: [], loadingAlternatives: false, saving: false,
      searchQuery: '', searchResults: [], searching: false,
      similar: [], loadingSimilar: false, mergeIds: [],
    },
    categoryOptions: CATEGORY_OPTIONS,

    segmentConvert: {
      open: false, segmentId: null, lat: null, lon: null,
      name: '', category: 'Other places', city: '', country: '', countryCode: '',
      searchQuery: '', searchResults: [], searching: false, saving: false,
    },

    // ─── formatters exposed to templates ───
    formatMiles,
    formatDuration,
    formatTime,
    formatDateRange,
    formatRelative,
    countryFlag,
    slugify,
    categoryIcon(cat) { return CATEGORY_ICONS[cat] || '\u{1F4CD}'; },
    modeIcon(mode) { return MODE_ICONS[mode] || 'move'; },
    entryLineHeight,

    init() {
      this.applyTheme(this.theme);
      this.loadDay();
    },

    setTheme(t) {
      this.theme = t;
      localStorage.setItem('waypoint-theme', t);
      this.applyTheme(t);
      this.themeMenuOpen = false;
    },
    applyTheme(t) {
      document.documentElement.setAttribute('data-theme', t);
    },

    switchTab(tab) {
      this.tab = tab;
      if (tab === 'day' && !this.day.data) this.loadDay();
      if (tab === 'trips' && !this.trips.data) this.loadTrips();
      if (tab === 'insights' && !this.insights.data) this.loadInsights();
      if (tab === 'places' && !this.places.data) this.loadPlaces();
      if (tab === 'cities' && !this.cities.data) this.loadCities();
      if (tab === 'world' && !this.world.data) this.loadWorld();

      this.$nextTick(() => {
        if (tab === 'day' && this.day.map) this.day.map.invalidateSize();
        if (tab === 'trips' && this.trips.map) this.trips.map.invalidateSize();
        if (tab === 'trips' && this.trips.detailMap) this.trips.detailMap.invalidateSize();
      });
    },

    // ─── Day ───
    async loadDay() {
      this.day.loading = true;
      try {
        const res = await fetch(`/api/day/${this.day.date}`);
        this.day.data = await res.json();
        this.$nextTick(() => this.renderDayMap());
      } catch (e) { console.error('Failed to load day', e); }
      finally { this.day.loading = false; }
    },
    changeDay(delta) {
      const d = new Date(this.day.date);
      d.setDate(d.getDate() + delta);
      this.day.date = d.toISOString().slice(0, 10);
      this.loadDay();
    },
    renderDayMap() {
      if (!this.day.map) this.day.map = wpInitMap('map-container');
      wpRenderDayMap(this.day.map, this.day.data.points, this.day.data.timeline);
    },
    dayStatModes() {
      if (!this.day.data) return [];
      const modes = new Set();
      for (const key of Object.keys(this.day.data.stats)) {
        if (key.endsWith('_m')) modes.add(key.slice(0, -2));
      }
      return [...modes].map((mode) => ({
        mode,
        distance_m: this.day.data.stats[`${mode}_m`] || 0,
        duration_s: this.day.data.stats[`${mode}_s`] || 0,
      }));
    },

    // ─── Trips ───
    async loadTrips() {
      this.trips.loading = true;
      try {
        const res = await fetch('/api/trips');
        this.trips.data = await res.json();
        this.$nextTick(() => this.renderTripsMap());
      } catch (e) { console.error('Failed to load trips', e); }
      finally { this.trips.loading = false; }
    },
    renderTripsMap() {
      if (!this.trips.map) this.trips.map = wpInitMap('trips-map-container');
      const pins = this.trips.data.trips.flatMap((t) =>
        t.visits.map((v) => ({ lat: v.lat, lon: v.lon, label: `<b>${v.place_name || t.primary_city || 'Visit'}</b>` }))
      );
      wpRenderPins(this.trips.map, pins);
    },
    async openTrip(tripId) {
      this.trips.detail = null;
      this.trips.detailLoading = true;
      try {
        const res = await fetch(`/api/trips/${tripId}`);
        this.trips.detail = await res.json();
        this.$nextTick(() => this.renderTripDetailMap());
      } catch (e) { console.error('Failed to load trip detail', e); }
      finally { this.trips.detailLoading = false; }
    },
    closeTrip() { this.trips.detail = null; },
    renderTripDetailMap() {
      if (!this.trips.detailMap) this.trips.detailMap = wpInitMap('trip-detail-map-container');
      wpRenderDayMap(this.trips.detailMap, [], this.trips.detail.timeline);
    },

    // ─── Insights ───
    async loadInsights() {
      this.insights.loading = true;
      try {
        const res = await fetch(`/api/insights/${this.insights.year}/${this.insights.month}`);
        this.insights.data = await res.json();
      } catch (e) { console.error('Failed to load insights', e); }
      finally { this.insights.loading = false; }
    },
    changeMonth(delta) {
      let m = this.insights.month + delta;
      let y = this.insights.year;
      if (m < 1) { m = 12; y -= 1; }
      if (m > 12) { m = 1; y += 1; }
      this.insights.month = m;
      this.insights.year = y;
      this.loadInsights();
    },
    monthLabel() {
      return new Date(this.insights.year, this.insights.month - 1, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
    },
    sparklineHeights(trend) {
      const max = Math.max(1, ...trend);
      return trend.map((v) => Math.max(4, Math.round((v / max) * 32)));
    },

    // ─── Places ───
    async loadPlaces() {
      this.places.loading = true;
      try {
        const res = await fetch('/api/places');
        this.places.data = await res.json();
      } catch (e) { console.error('Failed to load places', e); }
      finally { this.places.loading = false; }
    },
    async openCategory(cat) {
      this.places.category = cat;
      this.places.categoryData = null;
      const res = await fetch(`/api/places/${encodeURIComponent(cat)}`);
      this.places.categoryData = await res.json();
    },
    closeCategory() { this.places.category = null; this.places.categoryData = null; },

    // ─── Cities ───
    async loadCities() {
      this.cities.loading = true;
      try {
        const res = await fetch('/api/cities');
        this.cities.data = await res.json();
      } catch (e) { console.error('Failed to load cities', e); }
      finally { this.cities.loading = false; }
    },

    // ─── World ───
    async loadWorld() {
      this.world.loading = true;
      try {
        const res = await fetch('/api/world');
        this.world.data = await res.json();
      } catch (e) { console.error('Failed to load world', e); }
      finally { this.world.loading = false; }
    },

    // ─── Place correction ───
    // Nominatim only ever gives its single best guess, and that guess is
    // often wrong (a neighbouring building, a generic street match). This
    // lets you pick the right real place from what's actually nearby
    // (Overpass query server-side), or just type the correct name/category
    // yourself - matching Google Timeline's own "fix this place" flow.
    async openPlaceEdit(placeId, name, category, city, country, countryCode) {
      if (!placeId) return;
      this.placeEdit = {
        open: true, placeId, name: name || '', category: category || 'Other places',
        city: city || '', country: country || '', countryCode: countryCode || '',
        alternatives: [], loadingAlternatives: true, saving: false,
        searchQuery: '', searchResults: [], searching: false,
        similar: [], loadingSimilar: true, mergeIds: [],
      };
      try {
        const res = await fetch(`/api/places/detail/${placeId}/nearby`);
        const data = await res.json();
        this.placeEdit.alternatives = data.alternatives || [];
      } catch (e) { console.error('Failed to load nearby alternatives', e); }
      finally { this.placeEdit.loadingAlternatives = false; }

      try {
        const res = await fetch(`/api/places/detail/${placeId}/similar`);
        const data = await res.json();
        this.placeEdit.similar = data.similar || [];
      } catch (e) { console.error('Failed to load similar places', e); }
      finally { this.placeEdit.loadingSimilar = false; }
    },
    selectAlternative(alt) {
      // City/country are left as-is: alternatives are all within ~100m of
      // the same spot, so they're overwhelmingly likely to share the same
      // city/country as whatever was already resolved there.
      this.placeEdit.name = alt.name;
      this.placeEdit.category = alt.category;
    },
    async searchPlaces() {
      if (!this.placeEdit.searchQuery.trim()) return;
      this.placeEdit.searching = true;
      try {
        const params = new URLSearchParams({ q: this.placeEdit.searchQuery, place_id: this.placeEdit.placeId });
        const res = await fetch(`/api/places/search?${params}`);
        const data = await res.json();
        this.placeEdit.searchResults = data.results || [];
      } catch (e) { console.error('Failed to search places', e); }
      finally { this.placeEdit.searching = false; }
    },
    selectSearchResult(result) {
      // Unlike nearby alternatives, a free-text search result could be
      // anywhere - always take its city/country too, not just name/category.
      this.placeEdit.name = result.name;
      this.placeEdit.category = result.category;
      this.placeEdit.city = result.city || '';
      this.placeEdit.country = result.country || '';
      this.placeEdit.countryCode = result.country_code || '';
      this.placeEdit.searchResults = [];
      this.placeEdit.searchQuery = '';
    },
    toggleMerge(id) {
      const i = this.placeEdit.mergeIds.indexOf(id);
      if (i === -1) this.placeEdit.mergeIds.push(id);
      else this.placeEdit.mergeIds.splice(i, 1);
    },
    closePlaceEdit() { this.placeEdit.open = false; },
    async savePlaceEdit() {
      this.placeEdit.saving = true;
      try {
        const res = await fetch(`/api/places/detail/${this.placeEdit.placeId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: this.placeEdit.name,
            category: this.placeEdit.category,
            city: this.placeEdit.city || null,
            country: this.placeEdit.country || null,
            country_code: this.placeEdit.countryCode || null,
            merge_place_ids: this.placeEdit.mergeIds,
          }),
        });
        const data = await res.json();
        this.placeEdit.open = false;
        if (data.merged_visits) {
          console.log(`Merged ${this.placeEdit.mergeIds.length} place(s), repointed ${data.merged_visits} visit(s).`);
        }
        await this.refreshCurrentTab();
      } catch (e) { console.error('Failed to save place correction', e); }
      finally { this.placeEdit.saving = false; }
    },
    async refreshCurrentTab() {
      if (this.tab === 'day') return this.loadDay();
      if (this.tab === 'trips') return this.loadTrips();
      if (this.tab === 'places' && this.places.category) return this.openCategory(this.places.category);
      if (this.tab === 'places') return this.loadPlaces();
      if (this.tab === 'cities') return this.loadCities();
      if (this.tab === 'world') return this.loadWorld();
    },

    // ─── Timeline event editing ───
    // "Undo" here just means re-fetching the day - nothing is soft-deleted.
    async deleteVisit(entry) {
      if (!confirm(`Delete this visit (${entry.place_name || 'unnamed place'})? This can't be undone.`)) return;
      await Promise.all(entry.visit_ids.map((id) => fetch(`/api/events/visits/${id}`, { method: 'DELETE' })));
      await this.loadDay();
    },
    async deleteSegment(entry) {
      if (!confirm(`Delete this ${entry.mode} segment? This can't be undone.`)) return;
      await fetch(`/api/events/segments/${entry.id}`, { method: 'DELETE' });
      await this.loadDay();
    },
    canMergeUp(entry) {
      const visitEntries = this.day.data.timeline.filter((e) => e.type === 'visit');
      return visitEntries.indexOf(entry) > 0;
    },
    async mergeWithPrevious(entry) {
      if (!confirm(`Merge this visit into the previous one? This can't be undone.`)) return;
      await fetch(`/api/events/visits/${entry.visit_ids[0]}/merge-with-previous`, { method: 'POST' });
      await this.loadDay();
    },
    // Segment-to-visit conversion doesn't have a real coordinate of its own
    // (TripSegment stores distance/duration, not lat/lon) - approximates
    // with the midpoint of the visits either side, close enough for a search
    // bias and for placing the resulting Visit marker on the map.
    segmentMidpoint(entry) {
      const timeline = this.day.data.timeline;
      const idx = timeline.indexOf(entry);
      const prev = [...timeline.slice(0, idx)].reverse().find((e) => e.type === 'visit');
      const next = timeline.slice(idx + 1).find((e) => e.type === 'visit');
      if (!prev || !next) return null;
      return { lat: (prev.lat + next.lat) / 2, lon: (prev.lon + next.lon) / 2 };
    },
    openConvertSegment(entry) {
      const mid = this.segmentMidpoint(entry);
      if (!mid) { alert("Can't convert this segment - no visit on both sides to place it near."); return; }
      this.segmentConvert = {
        open: true, segmentId: entry.id, lat: mid.lat, lon: mid.lon,
        name: '', category: 'Other places', city: '', country: '', countryCode: '',
        searchQuery: '', searchResults: [], searching: false, saving: false,
      };
    },
    closeConvertSegment() { this.segmentConvert.open = false; },
    async searchConvertPlaces() {
      if (!this.segmentConvert.searchQuery.trim()) return;
      this.segmentConvert.searching = true;
      try {
        const params = new URLSearchParams({
          q: this.segmentConvert.searchQuery, lat: this.segmentConvert.lat, lon: this.segmentConvert.lon,
        });
        const res = await fetch(`/api/places/search?${params}`);
        const data = await res.json();
        this.segmentConvert.searchResults = data.results || [];
      } catch (e) { console.error('Failed to search places', e); }
      finally { this.segmentConvert.searching = false; }
    },
    selectConvertResult(result) {
      this.segmentConvert.name = result.name;
      this.segmentConvert.category = result.category;
      this.segmentConvert.city = result.city || '';
      this.segmentConvert.country = result.country || '';
      this.segmentConvert.countryCode = result.country_code || '';
      this.segmentConvert.lat = result.lat;
      this.segmentConvert.lon = result.lon;
      this.segmentConvert.searchResults = [];
      this.segmentConvert.searchQuery = '';
    },
    async saveConvertSegment() {
      if (!this.segmentConvert.name.trim()) { alert('Give the place a name first.'); return; }
      this.segmentConvert.saving = true;
      try {
        await fetch(`/api/events/segments/${this.segmentConvert.segmentId}/convert-to-visit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            lat: this.segmentConvert.lat, lon: this.segmentConvert.lon,
            name: this.segmentConvert.name, category: this.segmentConvert.category,
            city: this.segmentConvert.city || null, country: this.segmentConvert.country || null,
            country_code: this.segmentConvert.countryCode || null,
          }),
        });
        this.segmentConvert.open = false;
        await this.loadDay();
      } catch (e) { console.error('Failed to convert segment', e); }
      finally { this.segmentConvert.saving = false; }
    },
  };
}
