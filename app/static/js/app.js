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
  if (mi < 0.1) return '< 0.1 mi';
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
  'Food and drink': '\u{1F374}',
  'Shopping': '\u{1F6CD}',
  'Hotels': '\u{1F6CF}',
  'Culture': '\u{1F3DB}',
  'Sports': '\u{26BD}',
  'Airports': '✈',
  'Other places': '\u{1F4CD}',
};

const CATEGORY_OPTIONS = Object.keys(CATEGORY_ICONS);

function waypoint() {
  return {
    theme: localStorage.getItem('waypoint-theme') || 'atlas',
    themeMenuOpen: false,
    tab: 'day',

    day: { date: todayIso(), data: null, loading: false, map: null },
    trips: { data: null, loading: false, map: null },
    insights: { year: new Date().getFullYear(), month: new Date().getMonth() + 1, data: null, loading: false },
    places: { data: null, loading: false, category: null, categoryData: null },
    cities: { data: null, loading: false },
    world: { data: null, loading: false },

    placeEdit: {
      open: false, placeId: null, name: '', category: 'Other places', city: '', country: '', countryCode: '',
      alternatives: [], loadingAlternatives: false, saving: false,
    },
    categoryOptions: CATEGORY_OPTIONS,

    // ─── formatters exposed to templates ───
    formatMiles,
    formatDuration,
    formatTime,
    formatDateRange,
    formatRelative,
    countryFlag,
    slugify,
    categoryIcon(cat) { return CATEGORY_ICONS[cat] || '\u{1F4CD}'; },

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
      };
      try {
        const res = await fetch(`/api/places/detail/${placeId}/nearby`);
        const data = await res.json();
        this.placeEdit.alternatives = data.alternatives || [];
      } catch (e) { console.error('Failed to load nearby alternatives', e); }
      finally { this.placeEdit.loadingAlternatives = false; }
    },
    selectAlternative(alt) {
      // City/country are left as-is: alternatives are all within ~100m of
      // the same spot, so they're overwhelmingly likely to share the same
      // city/country as whatever was already resolved there.
      this.placeEdit.name = alt.name;
      this.placeEdit.category = alt.category;
    },
    closePlaceEdit() { this.placeEdit.open = false; },
    async savePlaceEdit() {
      this.placeEdit.saving = true;
      try {
        await fetch(`/api/places/detail/${this.placeEdit.placeId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: this.placeEdit.name,
            category: this.placeEdit.category,
            city: this.placeEdit.city || null,
            country: this.placeEdit.country || null,
            country_code: this.placeEdit.countryCode || null,
          }),
        });
        this.placeEdit.open = false;
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
  };
}
