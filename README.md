# Waypoint

A self-hosted replacement for Google Maps Timeline: your own location history,
processed into visits, trips, and stats, with no dependency on Google.

## What it is

Location pings come from [OwnTracks](https://owntracks.org/) (HTTP mode)
running on your phone, LAN-only by design - it queues points while you're
away and uploads the backlog once you're back on home Wi-Fi. Waypoint turns
those raw pings into:

- **Day** - a map and chronological list of visits and travel legs for any date
- **Trips** - multi-day stretches away from a configured home location
- **Insights** - monthly travel (walking/driving/flying) and visit-category stats
- **Places / Cities / World** - grids of everywhere you've been, grouped and counted

Reverse geocoding (place name, category, city, country) comes from the public
Nominatim API, cached indefinitely so the same spot is never looked up twice.

## Stack

FastAPI + SQLite backend, Alpine.js/Tailwind frontend, Leaflet for maps.
Single container, no build step needed to run it.

Two switchable themes ship out of the box - **Atlas** (dark dashboard) and
**Voyage** (vintage travel-journal) - built on a shared `--wp-*` CSS custom
property token layer, so adding a third theme later is just a new stylesheet.

## Running locally

```bash
python -m venv .venv
.venv/Scripts/activate      # or source .venv/bin/activate on Linux/Mac
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Or via Docker: `docker compose up --build`.

To try it out without a real phone feeding it, see
`scripts/seed_synthetic_data.py`.

## Deploying

See [DEPLOY.md](DEPLOY.md) for the rowan (home lab) deployment flow.
