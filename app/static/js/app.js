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
  };
}
