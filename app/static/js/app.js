function slugify(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

// Unicode flag emoji don't render as actual flags on Windows (no font
// support there) - it falls back to a generic placeholder glyph instead,
// the same one repeated for every country. A real flag image sidesteps
// that platform gap entirely. flagcdn.com is free, keyless, and serves by
// plain ISO 3166-1 alpha-2 code, which is exactly what country_code
// already is throughout this app.
function flagImageUrl(code, width) {
  if (!code || code.length !== 2) return '';
  return `https://flagcdn.com/w${width || 320}/${code.toLowerCase()}.png`;
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

// A plain "20:28 – 13:14" reads as same-day even when the end time is
// actually the next calendar day (or later) - confirmed live this was
// genuinely confusing (a Home visit still open at view time, an overnight
// flight), not a data bug. Appends how many calendar days later the end
// time falls, only when it's actually different from the start day.
function formatTimeRange(startTs, endTs) {
  const start = new Date(startTs * 1000);
  const end = new Date(endTs * 1000);
  const dayDiff = Math.round((new Date(end.toDateString()) - new Date(start.toDateString())) / 86400000);
  const range = `${formatTime(startTs)} – ${formatTime(endTs)}`;
  return dayDiff > 0 ? `${range} (+${dayDiff}d)` : range;
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
  'Banking and services': '\u{1F3E6}',
  'Education': '\u{1F393}',
  'Transport': '\u{1F68C}',
  'Nightlife': '\u{1F303}',
  'Healthcare': '\u{1F3E5}',
  'Entertainment': '\u{1F3AC}',
  'Parks and nature': '\u{1F333}',
  'Offices and services': '\u{1F3E2}',
  'Streets and roads': '\u{1F6E3}',
  'Other places': '\u{1F4CD}',
};

const CATEGORY_OPTIONS = Object.keys(CATEGORY_ICONS);

// --wp-cat-* token suffixes (theme-atlas.css/theme-voyage.css) don't match
// slugify(category) 1:1 (e.g. "Food and drink" -> token "food", not
// "food-and-drink" - the pill-cat-* classes already handle that mismatch
// with a dedicated class per category; this is the same mapping for
// Insights' per-category sparkline colour, which needs the raw var name
// rather than a class).
const CATEGORY_COLOR_VARS = {
  'Home': 'home',
  'Work': 'work',
  'Food and drink': 'food',
  'Shopping': 'shopping',
  'Hotels': 'hotels',
  'Culture': 'culture',
  'Sports': 'sports',
  'Airports': 'airports',
  'Banking and services': 'banking',
  'Education': 'education',
  'Transport': 'transport',
  'Nightlife': 'nightlife',
  'Healthcare': 'healthcare',
  'Entertainment': 'entertainment',
  'Parks and nature': 'parks',
  'Offices and services': 'offices',
  'Streets and roads': 'streets',
  'Other places': 'other',
};

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

    day: { date: todayIso(), data: null, loading: false, map: null, overview: null },
    trips: { data: null, loading: false, page: 1, map: null, detail: null, detailLoading: false, detailMap: null },
    insights: { year: new Date().getFullYear(), month: new Date().getMonth() + 1, data: null, loading: false },
    places: { data: null, loading: false, category: null, categoryData: null },
    cities: { data: null, loading: false, page: 1, detail: null, detailLoading: false, detailMap: null },
    world: { data: null, loading: false, map: null, detail: null, detailLoading: false, detailMap: null },

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
    formatTimeRange,
    formatDateRange,
    formatRelative,
    flagImageUrl,
    slugify,
    categoryIcon(cat) { return CATEGORY_ICONS[cat] || '\u{1F4CD}'; },
    modeIcon(mode) { return MODE_ICONS[mode] || 'move'; },
    entryLineHeight,

    // ─── card photos (Trips/Cities/Places) ───
    // Cache-busted per query so a manual refresh actually shows the new
    // photo immediately rather than the browser reusing its own cached
    // response for the same URL.
    imageCacheBust: {},
    // geo=true asserts this query is always a real place (a Trip/City name)
    // - see app/images.py's require_coordinates for why that's checked more
    // strictly than an arbitrary business/place name (Wikipedia's search
    // matched "Reading" to its article on the activity of reading, not the
    // town, since that's a real page title match on word overlap alone).
    // hint is an optional disambiguator (a country name, for a city/trip
    // query) - biases the search toward e.g. "Windsor, United Kingdom" over
    // Windsor, Ontario or the House of Windsor, all of which a bare
    // "Windsor" search can plausibly match. See app/images.py.
    imageUrl(query, fallback, geo, hint) {
      if (!query) return '';
      const v = this.imageCacheBust[query] || 0;
      const params = new URLSearchParams({ q: query, v });
      if (geo) params.set('geo', 'true');
      if (fallback && fallback !== query) params.set('fallback', fallback);
      if (hint && hint !== query) params.set('hint', hint);
      return `/api/images?${params}`;
    },
    async refreshCardImage(query, geo, hint) {
      if (!query) return;
      try {
        const params = new URLSearchParams({ q: query });
        if (geo) params.set('geo', 'true');
        if (hint && hint !== query) params.set('hint', hint);
        await fetch(`/api/images/refresh?${params}`, { method: 'POST' });
      } catch (e) { console.error('Failed to refresh image', e); }
      this.imageCacheBust[query] = (this.imageCacheBust[query] || 0) + 1;
    },
    // Upload your own photo - the alternative to re-searching online, for
    // when that finds nothing (or, worse, confidently finds the wrong
    // place - a business-name search matching an unrelated company, or a
    // "Windsor" search landing on Ontario instead of Berkshire). One
    // shared hidden <input type=file> (see index.html) is reused for every
    // card rather than one per card - triggerPhotoUpload just remembers
    // which query the eventual file selection is for.
    pendingUploadQuery: null,
    pendingUploadGeo: false,
    triggerPhotoUpload(query, geo) {
      this.pendingUploadQuery = query;
      this.pendingUploadGeo = !!geo;
      this.$refs.photoUploadInput.click();
    },
    async onPhotoFileSelected(event) {
      const file = event.target.files[0];
      event.target.value = ''; // otherwise picking the same file twice in a row doesn't fire 'change' again
      const query = this.pendingUploadQuery;
      this.pendingUploadQuery = null;
      if (!file || !query) return;
      try {
        const form = new FormData();
        form.append('file', file);
        const params = new URLSearchParams({ q: query });
        await fetch(`/api/images/upload?${params}`, { method: 'POST', body: form });
      } catch (e) { console.error('Failed to upload photo', e); }
      this.imageCacheBust[query] = (this.imageCacheBust[query] || 0) + 1;
      // Each card's imgFailed is local x-data state, not reachable from
      // here directly - this tells whichever card(s) currently showing
      // this query to try displaying an image again, the same way a
      // successful online refresh already does within its own click
      // handler's scope.
      window.dispatchEvent(new CustomEvent('wp-image-updated', { detail: { query } }));
    },

    appVersion: null,
    // Populated from window.__wpResourceFailures (see the inline listener at
    // the top of index.html's <head>, which starts catching failures before
    // Alpine even exists) plus the @wp-resource-failure.window listener on
    // the root element for anything that fails after this point (map tiles,
    // flag images - all loaded well after init()).
    resourceFailures: [],
    resourceFailuresDismissed: false,
    init() {
      this.resourceFailures = [...(window.__wpResourceFailures || [])];
      this.applyTheme(this.theme);
      fetch('/healthz').then((r) => r.json()).then((d) => { this.appVersion = d.version; }).catch(() => {});
      this.loadDay();
      this.loadDayOverview();
      // Opening a Trip/City/Country/category detail pushes a history entry
      // (see pushDetailState) purely so the browser's own Back button has
      // something of ours to pop first - without it, Back left the whole
      // app entirely and went to whatever page was open before Waypoint,
      // confirmed live as surprising ("shouldn't Back just return me to the
      // city list?"). The in-app "Back to X" buttons call history.back()
      // too now, so both paths go through this one place.
      window.addEventListener('popstate', () => this.closeAllDetails());
    },
    pushDetailState() {
      history.pushState({ wpDetail: true }, '');
    },
    closeAllDetails() {
      // Detail maps must be torn down here, not just have their container
      // div disappear - the div is destroyed/recreated by Alpine's x-if
      // every time (unlike x-show), but the Leaflet instance itself lived on
      // in trips.detailMap/etc, so re-opening a second city (or trip, or
      // country) reused a map bound to a DOM node that no longer existed
      // instead of creating a fresh one for the new container. Confirmed
      // live: a City detail opened a second time showed no map at all.
      if (this.trips.detailMap) { this.trips.detailMap.remove(); this.trips.detailMap = null; }
      if (this.cities.detailMap) { this.cities.detailMap.remove(); this.cities.detailMap = null; }
      if (this.world.detailMap) { this.world.detailMap.remove(); this.world.detailMap = null; }
      // Returning to the Trips/World overview has exactly the same problem
      // as above, one level up: its own map's container is also destroyed
      // and recreated by the x-if that hides/shows the overview while a
      // detail is open, confirmed live as a completely blank overview map
      // after coming back from a trip. Only torn down/re-rendered when a
      // detail was actually open, so this never runs pointlessly on every
      // popstate.
      const wasTripDetail = !!this.trips.detail;
      const wasCountryDetail = !!this.world.detail;
      this.trips.detail = null;
      this.cities.detail = null;
      this.world.detail = null;
      this.places.category = null;
      this.places.categoryData = null;
      if (wasTripDetail && this.trips.map) {
        this.trips.map.remove();
        this.trips.map = null;
        this.$nextTick(() => this.renderTripsMap());
      }
      if (wasCountryDetail && this.world.map) {
        this.world.map.remove();
        this.world.map = null;
        this.$nextTick(() => this.renderWorldMap());
      }
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
      if (tab === 'insights' && !this.insightsHighlights) this.loadInsightsHighlights();
      if (tab === 'insights' && !this.heatmapData) this.loadHeatmap();
      if (tab === 'places' && !this.places.data) this.loadPlaces();
      if (tab === 'cities' && !this.cities.data) this.loadCities();
      if (tab === 'world' && !this.world.data) this.loadWorld();

      this.$nextTick(() => {
        if (tab === 'day' && this.day.map) this.day.map.invalidateSize();
        if (tab === 'trips' && this.trips.map) this.trips.map.invalidateSize();
        if (tab === 'trips' && this.trips.detailMap) this.trips.detailMap.invalidateSize();
        if (tab === 'world' && this.world.map) this.world.map.invalidateSize();
        if (tab === 'world' && this.world.detailMap) this.world.detailMap.invalidateSize();
        if (tab === 'cities' && this.cities.detailMap) this.cities.detailMap.invalidateSize();
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
    goToDate(dateStr) {
      if (!dateStr) return;
      this.day.date = dateStr;
      this.loadDay();
    },
    async loadDayOverview() {
      try {
        const res = await fetch('/api/day/overview');
        this.day.overview = await res.json();
      } catch (e) { console.error('Failed to load day overview', e); }
    },
    // ─── Day history chart (replaces an earlier, cramped year-strip) ───
    // A monthly density chart across all recorded history - drag or click
    // anywhere to jump the Day view to that month, per Richard's explicit
    // choice of "build the full Emby History-style chart" over a bigger
    // year-strip or dropping it entirely.
    chartHover: null, // {x, month} - current playhead position while hovering/dragging
    chartDragging: false,
    chartMonths() {
      return (this.day.overview && this.day.overview.months) || [];
    },
    chartMax() {
      return Math.max(1, ...this.chartMonths().map((m) => m.visit_count));
    },
    // x runs 0-1000 (the SVG viewBox width) regardless of how many months
    // there are - keeps every coordinate helper below in the same units.
    chartX(index) {
      const n = this.chartMonths().length;
      return n > 1 ? (index / (n - 1)) * 1000 : 500;
    },
    chartY(count) {
      return 180 - (count / this.chartMax()) * 160;
    },
    chartAreaPath() {
      const months = this.chartMonths();
      if (!months.length) return '';
      const top = months.map((m, i) => `${this.chartX(i).toFixed(1)},${this.chartY(m.visit_count).toFixed(1)}`).join(' L ');
      return `M ${this.chartX(0).toFixed(1)},180 L ${top} L ${this.chartX(months.length - 1).toFixed(1)},180 Z`;
    },
    chartLinePath() {
      return this.chartMonths()
        .map((m, i) => `${i === 0 ? 'M' : 'L'} ${this.chartX(i).toFixed(1)},${this.chartY(m.visit_count).toFixed(1)}`)
        .join(' ');
    },
    chartPeakMonth() {
      const months = this.chartMonths();
      if (!months.length) return null;
      return months.reduce((a, b) => (b.visit_count > a.visit_count ? b : a), months[0]);
    },
    chartPeakPoint() {
      const months = this.chartMonths();
      const peak = this.chartPeakMonth();
      if (!peak || !peak.visit_count) return null;
      const idx = months.indexOf(peak);
      return { x: this.chartX(idx), y: this.chartY(peak.visit_count), month: peak };
    },
    // A tick per calendar year, thinned out once there'd be more than
    // ~10-14 labels crowding the axis (11+ years of history is common here).
    chartYearTicks() {
      const months = this.chartMonths();
      const ticks = [];
      let lastYear = null;
      months.forEach((m, i) => {
        const year = m.month.slice(0, 4);
        if (year !== lastYear) {
          ticks.push({ x: this.chartX(i), label: year });
          lastYear = year;
        }
      });
      if (ticks.length > 14) {
        const step = Math.ceil(ticks.length / 10);
        return ticks.filter((_, i) => i % step === 0);
      }
      return ticks;
    },
    chartStats() {
      const months = this.chartMonths();
      return {
        totalVisits: months.reduce((sum, m) => sum + m.visit_count, 0),
        activeMonths: months.filter((m) => m.visit_count > 0).length,
        firstMonth: months[0] || null,
        lastMonth: months[months.length - 1] || null,
        peak: this.chartPeakMonth(),
      };
    },
    // Named distinctly from the Insights tab's own monthLabel() (no args,
    // formats this.insights.year/month) - both were plain object-literal
    // methods with the same name, so the later one silently won for every
    // caller regardless of which was intended. Confirmed live: the chart's
    // peak/stat labels all showed "July 2026" (today, via the Insights
    // method's default state) no matter which month was actually peak.
    chartMonthLabel(monthStr) {
      const [y, m] = monthStr.split('-').map(Number);
      return new Date(y, m - 1, 1).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
    },
    // Pointer position -> nearest month, shared by click and drag.
    chartMonthAtEvent(evt) {
      const svg = evt.currentTarget.closest('svg');
      const rect = svg.getBoundingClientRect();
      const clientX = evt.touches ? evt.touches[0].clientX : evt.clientX;
      const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      const months = this.chartMonths();
      return months[Math.round(frac * (months.length - 1))] || null;
    },
    chartPointerDown(evt) {
      this.chartDragging = true;
      this.chartPointerMove(evt);
    },
    chartPointerMove(evt) {
      const month = this.chartMonthAtEvent(evt);
      if (!month) return;
      const idx = this.chartMonths().indexOf(month);
      this.chartHover = { x: this.chartX(idx), month };
      if (this.chartDragging) this.goToMonth(month);
    },
    chartPointerUp() {
      this.chartDragging = false;
    },
    chartPointerLeave() {
      if (!this.chartDragging) this.chartHover = null;
    },
    goToMonth(month) {
      // A zero-visit (gap-filled) month has no real date to jump to - the
      // playhead can still track the pointer over it, it just doesn't drag
      // the Day view along until it reaches a month that has data, the same
      // way a video scrubber can't seek into a stretch with no footage.
      if (!month || !month.min_ts) return;
      this.day.date = new Date(month.min_ts * 1000).toISOString().slice(0, 10);
      this.loadDay();
    },
    renderDayMap() {
      if (!this.day.map) this.day.map = wpInitMap('map-container');
      wpRenderDayMap(this.day.map, this.day.data.points, this.day.data.timeline, this.day.data.context_visits);
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
        const res = await fetch(`/api/trips?page=${this.trips.page}`);
        this.trips.data = await res.json();
        this.$nextTick(() => this.renderTripsMap());
      } catch (e) { console.error('Failed to load trips', e); }
      finally { this.trips.loading = false; }
    },
    changeTripsPage(delta) {
      const next = this.trips.page + delta;
      if (next < 1 || (this.trips.data && next > this.trips.data.total_pages)) return;
      this.trips.page = next;
      this.loadTrips();
    },
    // Overview map shows pins for the current page's destinations only, not
    // all 800 trips at once - that many markers was unwieldy anyway, and
    // the World tab already covers the "whole history at a glance" view.
    renderTripsMap() {
      if (!this.trips.map) this.trips.map = wpInitMap('trips-map-container');
      const pins = this.trips.data.destinations.flatMap((d) =>
        d.trips.flatMap((t) =>
          t.visits.map((v) => ({ lat: v.lat, lon: v.lon, category: v.category, label: `<b>${v.place_name || d.primary_city || 'Visit'}</b>` }))
        )
      );
      wpRenderPins(this.trips.map, pins);
    },
    async openTrip(tripId) {
      this.pushDetailState();
      this.trips.detail = null;
      this.trips.detailLoading = true;
      try {
        const res = await fetch(`/api/trips/${tripId}`);
        this.trips.detail = await res.json();
        this.$nextTick(() => this.renderTripDetailMap());
      } catch (e) { console.error('Failed to load trip detail', e); }
      finally { this.trips.detailLoading = false; }
    },
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
    // Small area+line trend chart per tile, in the same visual language as
    // the Day view's history chart (rather than the plain bar sparkline
    // this replaces) - ties Insights into the same design family instead of
    // reading as noticeably plainer than every other tab, per feedback.
    // viewBox is 0-100 wide, 0-32 tall regardless of point count.
    sparklinePath(trend, kind) {
      const max = Math.max(1, ...trend);
      const n = trend.length;
      const pts = trend.map((v, i) => [n > 1 ? (i / (n - 1)) * 100 : 50, 32 - (v / max) * 28]);
      if (kind === 'area') {
        const top = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L ');
        return `M ${pts[0][0].toFixed(1)},32 L ${top} L ${pts[pts.length - 1][0].toFixed(1)},32 Z`;
      }
      return pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    },
    // Month-on-month change, for a small up/down indicator next to the
    // value - trend's last element is always the month currently being
    // viewed (see app/api/insights.py's _step_back), so trend[-2] is the
    // one immediately before it. null when there's nothing to compare
    // against (no data last month), rather than showing a meaningless
    // "+inf%" or dividing by zero.
    trendDelta(trend) {
      const current = trend[trend.length - 1] || 0;
      const previous = trend[trend.length - 2] || 0;
      if (!previous) return null;
      return Math.round(((current - previous) / previous) * 100);
    },
    categoryColorVar(category) {
      const key = CATEGORY_COLOR_VARS[category] || 'other';
      return `var(--wp-cat-${key})`;
    },

    // ─── Insights: all-time highlights + activity heatmap ───
    // Not scoped to insights.year/month - these are all-time records, loaded
    // once (like day.overview) rather than re-fetched on every month change.
    insightsHighlights: null,
    async loadInsightsHighlights() {
      try {
        const res = await fetch('/api/insights/highlights');
        this.insightsHighlights = await res.json();
      } catch (e) { console.error('Failed to load insights highlights', e); }
    },
    formatDayString(dayStr) {
      const [y, m, d] = dayStr.split('-').map(Number);
      return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
    },

    heatmapYear: new Date().getFullYear(),
    heatmapData: null,
    async loadHeatmap() {
      try {
        const res = await fetch(`/api/insights/heatmap/${this.heatmapYear}`);
        this.heatmapData = await res.json();
      } catch (e) { console.error('Failed to load heatmap', e); }
    },
    changeHeatmapYear(delta) {
      this.heatmapYear += delta;
      this.heatmapData = null;
      this.loadHeatmap();
    },
    // GitHub-contributions-style grid: one column per week, one row per
    // weekday (Monday first). Front-padded with null slots so day one of
    // the year lines up under its real weekday rather than always starting
    // at the grid's top-left regardless of what day 1 January actually was.
    heatmapWeeks() {
      if (!this.heatmapData) return [];
      const days = this.heatmapData.days;
      const max = Math.max(1, ...days.map((d) => d.visit_count));
      const firstDate = new Date(days[0].date + 'T00:00:00');
      const firstWeekday = (firstDate.getDay() + 6) % 7; // Sun=0..Sat=6 -> Mon=0..Sun=6
      const padded = Array(firstWeekday).fill(null).concat(days.map((d) => ({ ...d, intensity: d.visit_count / max })));
      const weeks = [];
      for (let i = 0; i < padded.length; i += 7) weeks.push(padded.slice(i, i + 7));
      return weeks;
    },
    heatmapCellStyle(day) {
      if (!day || !day.visit_count) return '';
      return `background: var(--wp-accent); opacity: ${(0.25 + day.intensity * 0.75).toFixed(2)}`;
    },
    heatmapCellTitle(day) {
      if (!day) return '';
      return `${this.formatDayString(day.date)} · ${day.visit_count} visit${day.visit_count === 1 ? '' : 's'}`;
    },
    // One label per week-column, only on the week a new month actually
    // starts in - matches the GitHub contributions graph's own convention,
    // rather than a label on every column (illegible at this cell size) or
    // a single fixed label per fortnight (wouldn't line up with real month
    // boundaries).
    heatmapMonthLabels() {
      return this.heatmapWeeks().map((week) => {
        const firstOfMonth = week.find((d) => d && d.date.endsWith('-01'));
        if (!firstOfMonth) return '';
        const [y, m] = firstOfMonth.date.split('-').map(Number);
        return new Date(y, m - 1, 1).toLocaleDateString('en-GB', { month: 'short' });
      });
    },
    openHeatmapDay(day) {
      if (!day) return;
      this.switchTab('day');
      this.goToDate(day.date);
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
      this.pushDetailState();
      this.places.category = cat;
      this.places.categoryData = null;
      const res = await fetch(`/api/places/${encodeURIComponent(cat)}`);
      this.places.categoryData = await res.json();
    },
    placeVisits: {},
    async togglePlaceVisits(placeId) {
      if (this.placeVisits[placeId]) {
        delete this.placeVisits[placeId];
        return;
      }
      this.placeVisits[placeId] = { loading: true, visits: [] };
      try {
        const res = await fetch(`/api/places/detail/${placeId}/visits`);
        const data = await res.json();
        this.placeVisits[placeId] = { loading: false, visits: data.visits };
      } catch (e) { console.error('Failed to load place visits', e); delete this.placeVisits[placeId]; }
    },

    // ─── Cities ───
    async loadCities() {
      this.cities.loading = true;
      try {
        const res = await fetch(`/api/cities?page=${this.cities.page}`);
        this.cities.data = await res.json();
      } catch (e) { console.error('Failed to load cities', e); }
      finally { this.cities.loading = false; }
    },
    changeCitiesPage(delta) {
      const next = this.cities.page + delta;
      if (next < 1 || (this.cities.data && next > this.cities.data.total_pages)) return;
      this.cities.page = next;
      this.loadCities();
    },
    async openCity(cityName) {
      if (!cityName) return;
      this.pushDetailState();
      this.cities.detail = null;
      this.cities.detailLoading = true;
      try {
        const res = await fetch(`/api/cities/${encodeURIComponent(cityName)}`);
        this.cities.detail = await res.json();
        this.$nextTick(() => this.renderCityMap());
      } catch (e) { console.error('Failed to load city detail', e); }
      finally { this.cities.detailLoading = false; }
    },
    renderCityMap() {
      if (!this.cities.detailMap) this.cities.detailMap = wpInitMap('city-detail-map-container');
      const pins = this.cities.detail.places.map((p) => ({
        lat: p.lat, lon: p.lon, category: p.category,
        label: `<b>${p.name || 'Unnamed place'}</b>`,
      }));
      wpRenderPins(this.cities.detailMap, pins);
    },
    // Same as openCity, but switches to the Cities tab first - used by
    // World's Country detail city cards, which were deliberately left
    // non-clickable earlier (crossing from the World tab into Cities felt
    // like more state-juggling than it was worth) until asked for directly.
    openCityFromCountry(cityName) {
      this.switchTab('cities');
      this.openCity(cityName);
    },

    // ─── World ───
    async loadWorld() {
      this.world.loading = true;
      try {
        const res = await fetch('/api/world');
        this.world.data = await res.json();
        this.$nextTick(() => this.renderWorldMap());
      } catch (e) { console.error('Failed to load world', e); }
      finally { this.world.loading = false; }
    },
    renderWorldMap() {
      if (!this.world.map) this.world.map = wpInitMap('world-map-container', { minZoom: 0 });
      const visitedCodes = new Set(this.world.data.countries.map((c) => c.country_code).filter(Boolean));
      wpRenderWorldMap(this.world.map, visitedCodes);
    },
    async openCountry(countryCode) {
      if (!countryCode) return;
      this.pushDetailState();
      this.world.detail = null;
      this.world.detailLoading = true;
      try {
        const res = await fetch(`/api/world/${countryCode}`);
        this.world.detail = await res.json();
        this.$nextTick(() => this.renderCountryMap());
      } catch (e) { console.error('Failed to load country detail', e); }
      finally { this.world.detailLoading = false; }
    },
    renderCountryMap() {
      if (!this.world.detailMap) this.world.detailMap = wpInitMap('country-detail-map-container');
      const pins = this.world.detail.pins.map((p) => ({
        lat: p.lat, lon: p.lon, category: p.category,
        label: `<b>${p.name || 'Unnamed place'}</b><br>${p.city || ''}`,
      }));
      wpRenderPins(this.world.detailMap, pins);
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
        city: city || '', country: country || '', countryCode: countryCode || '', googlePlaceId: '',
        alternatives: [], googleAlternatives: [], loadingAlternatives: true, saving: false,
        searchQuery: '', searchResults: [], searching: false,
        similar: [], loadingSimilar: true, mergeIds: [],
      };
      try {
        const res = await fetch(`/api/places/detail/${placeId}/nearby`);
        const data = await res.json();
        this.placeEdit.alternatives = data.alternatives || [];
        this.placeEdit.googleAlternatives = data.google_alternatives || [];
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
    selectGoogleAlternative(alt) {
      // Same reasoning as selectAlternative (city/country left as-is), plus
      // the placeId itself - worth keeping on the row even for a manual
      // correction, so a future Google-sourced backfill re-run recognises
      // this place as already resolved rather than having nothing to check.
      this.placeEdit.name = alt.name;
      this.placeEdit.category = alt.category;
      this.placeEdit.googlePlaceId = alt.google_place_id;
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
            google_place_id: this.placeEdit.googlePlaceId || null,
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
    // Speed alone can't tell a taxi from a car, or a tram from a train - this
    // is the only way to correct the classifier's best guess once you know
    // better.
    async updateSegmentMode(entry, mode) {
      if (!mode || mode === entry.mode) return;
      try {
        await fetch(`/api/events/segments/${entry.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode }),
        });
        await this.loadDay();
      } catch (e) { console.error('Failed to update segment mode', e); }
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
