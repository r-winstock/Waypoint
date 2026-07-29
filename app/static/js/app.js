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

// photo.taken_at is PhotoPrism's own ISO 8601 UTC string, not a unix
// timestamp like every other date field in this app - kept as its own
// formatter rather than routing it through the timestamp-based ones above.
function formatPhotoDate(takenAt) {
  if (!takenAt) return '';
  const d = new Date(takenAt);
  if (isNaN(d)) return '';
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
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
    insights: { year: new Date().getFullYear(), month: new Date().getMonth() + 1, data: null, loading: false, subtab: 'overview' },
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

    photoViewer: { open: false, photo: null },
    openPhotoViewer(photo) { this.photoViewer = { open: true, photo }; },
    closePhotoViewer() { this.photoViewer.open = false; },

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
      if (tab === 'insights' && !this.insightsStories) this.loadInsightsStories();
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

    // Overview and Records both only need insightsHighlights (already loaded
    // eagerly on tab entry, same as before this subtab split existed) -
    // Trends and Breakdown each need their own all-time endpoint, lazy-
    // loaded the first time that subtab is actually opened rather than on
    // every Insights visit regardless of which subtab is shown.
    switchInsightsSubtab(subtab) {
      this.insights.subtab = subtab;
      if (subtab === 'trends' && !this.insightsYearly) this.loadInsightsYearly();
      if (subtab === 'trends' && !this.insightsSeasonality) this.loadInsightsSeasonality();
      if (subtab === 'breakdown' && !this.insightsBreakdown) this.loadInsightsBreakdown();
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
      wpRenderDayMap(this.day.map, this.day.data.points, this.day.data.timeline, this.day.data.context_visits, this.allTimelinePhotos(this.day.data), (photo) => this.openPhotoViewer(photo));
    },
    // `data.photos` is only the leftover photos that didn't match any
    // visit's own time window (see attach_photos_to_visits) - most photos
    // live on their matching timeline entry instead, for the per-visit
    // gallery strips. The map still wants every photo plotted regardless
    // of which gallery (if any) it ended up in, so this recombines both
    // rather than the map silently losing markers for anything attached
    // to a visit.
    allTimelinePhotos(data) {
      const fromEntries = (data.timeline || []).filter((e) => e.photos && e.photos.length).flatMap((e) => e.photos);
      return (data.photos || []).concat(fromEntries);
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
      wpRenderDayMap(this.trips.detailMap, [], this.trips.detail.timeline, {}, this.allTimelinePhotos(this.trips.detail), (photo) => this.openPhotoViewer(photo));
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
    // Earth's mean circumference, 40,075 km - a fun/narrative comparison for
    // the Overview subtab's total-distance stat, computed client-side since
    // it's a pure display transform of a value the backend already returns.
    earthCircumferences() {
      if (!this.insightsHighlights) return null;
      return (this.insightsHighlights.total_distance_m / 40075000).toFixed(1);
    },
    // stroke-dasharray on the gauge's semicircle path is fixed at 282.74
    // (pi * 90, the path's own radius - see the <path> in index.html) -
    // this is the matching stroke-dashoffset for a given percentage.
    lifeGaugeOffset(percent) {
      const frac = Math.min(1, Math.max(0, (percent || 0) / 100));
      return (282.74 * (1 - frac)).toFixed(2);
    },
    // This app has no Settings UI at all yet (home_lat/lon are equally
    // curl-only, per DEPLOY.md) - shown directly in the empty state rather
    // than silently omitting the life-percent stat with no way to discover
    // how to enable it.
    birthDateCurlCommand() {
      return `curl -X PUT ${location.origin}/api/settings -H "Content-Type: application/json" -d '{"birth_date": "YYYY-MM-DD"}'`;
    },

    // ─── Insights: year-over-year trend (Trends subtab) ───
    // Mirrors the Day view's own history chart (chartX/Y/AreaPath/LinePath
    // etc.) as closely as possible for visual/behavioural consistency, just
    // sourced from /api/insights/yearly (one point per year) instead of
    // /api/day/overview (one point per month) - no click-to-navigate here,
    // since there's no equivalent "jump to this year" action for Insights
    // the way the Day view jumps to a clicked month.
    insightsYearly: null,
    insightsYearlyHover: null,
    async loadInsightsYearly() {
      try {
        const res = await fetch('/api/insights/yearly');
        this.insightsYearly = await res.json();
      } catch (e) { console.error('Failed to load yearly insights', e); }
    },
    insightsYearlyYears() {
      return (this.insightsYearly && this.insightsYearly.years) || [];
    },
    insightsYearlyMax() {
      return Math.max(1, ...this.insightsYearlyYears().map((y) => y.distance_m));
    },
    insightsYearlyX(index) {
      const n = this.insightsYearlyYears().length;
      return n > 1 ? (index / (n - 1)) * 1000 : 500;
    },
    insightsYearlyY(distanceM) {
      return 180 - (distanceM / this.insightsYearlyMax()) * 160;
    },
    insightsYearlyAreaPath() {
      const years = this.insightsYearlyYears();
      if (!years.length) return '';
      const top = years.map((y, i) => `${this.insightsYearlyX(i).toFixed(1)},${this.insightsYearlyY(y.distance_m).toFixed(1)}`).join(' L ');
      return `M ${this.insightsYearlyX(0).toFixed(1)},180 L ${top} L ${this.insightsYearlyX(years.length - 1).toFixed(1)},180 Z`;
    },
    insightsYearlyLinePath() {
      return this.insightsYearlyYears()
        .map((y, i) => `${i === 0 ? 'M' : 'L'} ${this.insightsYearlyX(i).toFixed(1)},${this.insightsYearlyY(y.distance_m).toFixed(1)}`)
        .join(' ');
    },
    insightsYearlyPeak() {
      const years = this.insightsYearlyYears();
      if (!years.length) return null;
      return years.reduce((a, b) => (b.distance_m > a.distance_m ? b : a), years[0]);
    },
    insightsYearlyPeakPoint() {
      const years = this.insightsYearlyYears();
      const peak = this.insightsYearlyPeak();
      if (!peak || !peak.distance_m) return null;
      const idx = years.indexOf(peak);
      return { x: this.insightsYearlyX(idx), y: this.insightsYearlyY(peak.distance_m), year: peak };
    },
    insightsYearlyTicks() {
      const years = this.insightsYearlyYears();
      const ticks = years.map((y, i) => ({ x: this.insightsYearlyX(i), label: String(y.year) }));
      if (ticks.length > 14) {
        const step = Math.ceil(ticks.length / 10);
        return ticks.filter((_, i) => i % step === 0);
      }
      return ticks;
    },
    insightsYearlyPointerMove(evt) {
      const years = this.insightsYearlyYears();
      if (!years.length) return;
      const rect = evt.currentTarget.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width));
      const idx = Math.round(frac * (years.length - 1));
      this.insightsYearlyHover = { x: this.insightsYearlyX(idx), year: years[idx] };
    },
    insightsYearlyPointerLeave() {
      this.insightsYearlyHover = null;
    },
    // A dot per year, as one static path (a pair of arcs per point) rather
    // than a <template x-for> inside the <svg> - confirmed elsewhere in this
    // file (the Day view's own history chart) that Alpine's x-for/x-if
    // don't reliably work inside SVG/foreign content in this environment.
    insightsYearlyDotsPath() {
      const r = 3;
      return this.insightsYearlyYears()
        .map((y, i) => {
          const x = this.insightsYearlyX(i);
          const cy = this.insightsYearlyY(y.distance_m);
          return `M ${(x - r).toFixed(1)},${cy.toFixed(1)} a ${r},${r} 0 1,0 ${r * 2},0 a ${r},${r} 0 1,0 ${-r * 2},0`;
        })
        .join(' ');
    },

    // ─── Insights: seasonality (Trends subtab) ───
    // "Which calendar month do you travel most", aggregated across every
    // year of history - an emphasis-form bar chart (the peak month in
    // accent colour, the rest muted) rather than a categorical palette,
    // since there's exactly one point to this chart: which month stands out.
    insightsSeasonality: null,
    async loadInsightsSeasonality() {
      try {
        const res = await fetch('/api/insights/seasonality');
        this.insightsSeasonality = await res.json();
      } catch (e) { console.error('Failed to load seasonality insights', e); }
    },
    insightsSeasonalityMonths() {
      return (this.insightsSeasonality && this.insightsSeasonality.months) || [];
    },
    insightsSeasonalityMax() {
      return Math.max(1, ...this.insightsSeasonalityMonths().map((m) => m.distance_m));
    },
    insightsSeasonalityPeakMonth() {
      const months = this.insightsSeasonalityMonths();
      if (!months.length) return null;
      const peak = months.reduce((a, b) => (b.distance_m > a.distance_m ? b : a), months[0]);
      return peak.distance_m > 0 ? peak : null;
    },
    insightsSeasonalityBarHeight(m) {
      return Math.max(2, (m.distance_m / this.insightsSeasonalityMax()) * 120);
    },
    monthName(monthNum, short) {
      const d = new Date(2000, monthNum - 1, 1);
      return d.toLocaleDateString('en-GB', { month: short ? 'short' : 'long' });
    },
    // Northern-hemisphere seasons - a fair assumption for this specific,
    // personal, UK-based deployment (every trip in this dataset originates
    // from home in England), not something this app would want to guess at
    // for an arbitrary user elsewhere in the world.
    seasonEmoji(monthNum) {
      if ([12, 1, 2].includes(monthNum)) return '❄️';
      if ([3, 4, 5].includes(monthNum)) return '🌸';
      if ([6, 7, 8].includes(monthNum)) return '☀️';
      return '🍂';
    },

    // ─── Insights: all-time breakdown (Breakdown subtab) ───
    // Same underlying totals the month-scoped tiles below already show,
    // just summed across the whole of history instead of one month - a
    // ranked horizontal bar list, not a donut: Waypoint's own category
    // taxonomy runs to ~18 categories, and a many-slice pie/donut is a
    // known-bad form for that count (see the dataviz skill's own series-
    // count ladder) - a sorted bar list stays readable regardless of how
    // many categories actually have data.
    insightsBreakdown: null,
    async loadInsightsBreakdown() {
      try {
        const res = await fetch('/api/insights/breakdown');
        this.insightsBreakdown = await res.json();
      } catch (e) { console.error('Failed to load insights breakdown', e); }
    },
    insightsBreakdownTravelSorted() {
      const travel = (this.insightsBreakdown && this.insightsBreakdown.travel) || {};
      return Object.entries(travel)
        .map(([mode, t]) => ({ mode, ...t }))
        .sort((a, b) => b.distance_m - a.distance_m);
    },
    insightsBreakdownVisitsSorted() {
      const visits = (this.insightsBreakdown && this.insightsBreakdown.visits) || {};
      return Object.entries(visits)
        .map(([category, v]) => ({ category, ...v }))
        .sort((a, b) => b.duration_s - a.duration_s);
    },
    insightsBreakdownTravelMax() {
      return Math.max(1, ...this.insightsBreakdownTravelSorted().map((t) => t.distance_m));
    },
    insightsBreakdownVisitsMax() {
      return Math.max(1, ...this.insightsBreakdownVisitsSorted().map((v) => v.duration_s));
    },

    // ─── Insights: "did you know" story card (Overview subtab hero) ───
    // Real computed narrative sentences, not another stat tile - per
    // Richard's own feedback that the first version, however accurate,
    // read as a report about him rather than something he was part of: "I
    // wanna feel PART of the stats, not a reader." Three concrete changes
    // from that feedback: (1) paging is entirely user-driven (dots/arrows),
    // no auto-advance timer running a slideshow at the viewer: intrigue
    // needs delivered at the viewer's own pace, not on someone else's
    // clock. (2) the card body itself is a doorway, not a caption - clicking
    // it (see storyNavigate) lands on the actual Trip/Place/Country the
    // story is about wherever one genuinely exists, rather than every
    // insight being a dead end. (3) a real photo background wherever the
    // story has one true subject to show (see storyImageQuery), reusing
    // the exact same imageUrl() system already powering Trip/Place/City
    // cards, rather than a generic icon standing in for the place itself.
    insightsStories: null,
    storyIndex: 0,
    storyImgFailed: false,
    async loadInsightsStories() {
      try {
        const res = await fetch('/api/insights/stories');
        const data = await res.json();
        // Fisher-Yates - shuffled once per load so the paging order isn't
        // the same every time, not re-shuffled on every page (that would
        // make "next" jump to an already-seen story).
        const stories = data.stories;
        for (let i = stories.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [stories[i], stories[j]] = [stories[j], stories[i]];
        }
        this.insightsStories = stories;
        this.storyIndex = 0;
        this.storyImgFailed = false;
      } catch (e) { console.error('Failed to load insights stories', e); }
    },
    nextStory() {
      if (!this.insightsStories || !this.insightsStories.length) return;
      this.storyIndex = (this.storyIndex + 1) % this.insightsStories.length;
      this.storyImgFailed = false;
    },
    prevStory() {
      if (!this.insightsStories || !this.insightsStories.length) return;
      this.storyIndex = (this.storyIndex - 1 + this.insightsStories.length) % this.insightsStories.length;
      this.storyImgFailed = false;
    },
    goToStory(i) {
      this.storyIndex = i;
      this.storyImgFailed = false;
    },
    currentStory() {
      return this.insightsStories && this.insightsStories.length ? this.insightsStories[this.storyIndex] : null;
    },
    // Only the story types with one real, specific subject get a photo -
    // "no image beats a wrong one", the same principle images.py's own
    // fetch pipeline already follows for card photos generally.
    storyImageQuery(story) {
      if (!story) return null;
      switch (story.type) {
        case 'most_visited':
          return { query: story.name, fallback: story.city, geo: false, hint: story.city };
        case 'longest_trip':
          return { query: story.primary_city || story.primary_country, fallback: null, geo: true, hint: story.primary_country };
        case 'newest_country':
          return { query: story.country, fallback: null, geo: true, hint: null };
        default:
          return null;
      }
    },
    // Which story types land somewhere real on click - deliberately not
    // every type. peak_year/peak_month/longest_gap/moon_trips/country_count
    // etc. describe a pattern across the whole dataset, not one specific
    // Trip/Place/Country to land on - forcing a fake destination for those
    // would be worse than simply not making them clickable.
    storyHasNav(story) {
      return !!story && [
        'most_visited', 'longest_trip', 'busiest_day', 'newest_country', 'top_category',
      ].includes(story.type);
    },
    async storyNavigate(story) {
      if (!story || !this.storyHasNav(story)) return;
      switch (story.type) {
        case 'most_visited':
          this.switchTab('places');
          await this.openCategory(story.category);
          this.togglePlaceVisits(story.place_id);
          break;
        case 'longest_trip':
          this.switchTab('trips');
          this.openTrip(story.trip_id);
          break;
        case 'busiest_day':
          this.switchTab('day');
          this.goToDate(story.day);
          break;
        case 'newest_country':
          this.switchTab('world');
          this.openCountry(story.country_code);
          break;
        case 'top_category':
          this.switchTab('places');
          this.openCategory(story.category);
          break;
        default:
          break;
      }
    },
    // Shared click-through for any Records card that lands on a specific
    // Place (most-visited, farthest-from-home) - same three-step navigation
    // storyNavigate's own 'most_visited' case already uses, factored out so
    // Records doesn't have to duplicate it per card.
    async openPlaceRecord(record) {
      if (!record || !record.place_id) return;
      this.switchTab('places');
      await this.openCategory(record.category);
      this.togglePlaceVisits(record.place_id);
    },
    storyIcon(type) {
      const icons = {
        circumference: '🌍', most_visited: '📍', longest_trip: '🧳', busiest_day: '📅',
        peak_year: '📈', peak_month: '☀️', longest_gap: '🏠', country_count: '🌎',
        newest_country: '🆕', top_category: '⭐', moon_trips: '🌙', trip_frequency: '✈️',
      };
      return icons[type] || '💭';
    },
    storyText(story) {
      switch (story.type) {
        case 'circumference':
          return `You've travelled ${formatMiles(story.total_distance_m)} in total, enough to circle the Earth ${(story.total_distance_m / 40075000).toFixed(1)} times over.`;
        case 'most_visited':
          return `Nowhere beats ${story.name}${story.city ? ' in ' + story.city : ''}. You've been back ${story.visit_count} times.`;
        case 'longest_trip':
          return `Your longest trip yet: ${story.days} day${story.days === 1 ? '' : 's'} in ${story.primary_city || story.primary_country || 'one place'}.`;
        case 'busiest_day':
          return `Your busiest day ever was ${this.formatDayString(story.day)}, with ${story.visit_count} separate visits packed in.`;
        case 'peak_year':
          return `${story.year} was your biggest travel year yet, covering ${formatMiles(story.distance_m)}.`;
        case 'peak_month':
          return `You're clearly a ${this.monthName(story.month)} traveller. More distance covered then than any other month.`;
        case 'longest_gap':
          return `The longest you've ever gone without a trip is ${story.days} days.`;
        case 'country_count':
          return `You've set foot in ${story.count} countries, roughly ${story.percent_of_world}% of every country on Earth.`;
        case 'newest_country':
          return `Your most recently discovered country is ${story.country}, first visited in ${story.year}.`;
        case 'top_category':
          return `Away from home, you spend more time in ${story.category.toLowerCase()} than anywhere else.`;
        case 'moon_trips': {
          const trips = (story.flying_m / 384400000).toFixed(1);
          return `You've flown far enough to reach the Moon ${trips} time${trips === '1.0' ? '' : 's'} over.`;
        }
        case 'trip_frequency':
          return `You've taken ${story.trip_count} trips, averaging ${story.avg_trip_days} days each.`;
        default:
          return '';
      }
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
      this.placeVisits[placeId] = { loading: true, visits: [], photos: [] };
      try {
        const res = await fetch(`/api/places/detail/${placeId}/visits`);
        const data = await res.json();
        this.placeVisits[placeId] = { loading: false, visits: data.visits, photos: data.photos || [] };
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
