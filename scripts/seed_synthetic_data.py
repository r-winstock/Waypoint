"""Posts a plausible few days of OwnTracks-style pings to a locally running
Waypoint instance, so the six dashboard views can be exercised end to end
without waiting for real phone data. Not part of the deployed image.

Usage: python scripts/seed_synthetic_data.py [base_url]
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
AUTH = ("waypoint", "waypoint")

# Bedford, UK - used as "home" for trip-grouping.
HOME = (52.1362, -0.4667)
SHOP = (52.1450, -0.4720)       # ~1km away - "Shopping"
CAFE = (52.1300, -0.4550)       # ~1.5km away - "Food and drink"
STANSTED = (51.8850, 0.2350)    # near Stansted Airport, for the drive-to-airport leg
DUBLIN_HOTEL = (53.3498, -6.2603)
DUBLIN_RESTAURANT = (53.3410, -6.2670)

points: list[dict] = []


def add_stay(lat: float, lon: float, start: datetime, duration_s: int, interval_s: int = 600):
    t = 0
    while t <= duration_s:
        ts = start + timedelta(seconds=t)
        points.append({"lat": lat + (t % 3) * 0.00003, "lon": lon, "tst": int(ts.timestamp()), "vel": 0, "batt": 80})
        t += interval_s


def add_travel(lat1, lon1, lat2, lon2, start: datetime, duration_s: int, n: int = 8):
    for i in range(n + 1):
        frac = i / n
        ts = start + timedelta(seconds=duration_s * frac)
        lat = lat1 + (lat2 - lat1) * frac
        lon = lon1 + (lon2 - lon1) * frac
        points.append({"lat": lat, "lon": lon, "tst": int(ts.timestamp()), "vel": 0, "batt": 75})


now = datetime.now(timezone.utc)
today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

# ── Today: home -> shop -> cafe -> home ──
add_stay(*HOME, today_start + timedelta(hours=0), 8 * 3600)  # 00:00-08:00 at home
add_travel(*HOME, *SHOP, today_start + timedelta(hours=8), 20 * 60)  # 08:00-08:20 drive
add_stay(*SHOP, today_start + timedelta(hours=8, minutes=20), 90 * 60)  # shop 08:20-09:50
add_travel(*SHOP, *CAFE, today_start + timedelta(hours=9, minutes=50), 15 * 60)  # drive
add_stay(*CAFE, today_start + timedelta(hours=10, minutes=5), 60 * 60)  # cafe
add_travel(*CAFE, *HOME, today_start + timedelta(hours=11, minutes=5), 20 * 60)  # drive home
add_stay(*HOME, today_start + timedelta(hours=11, minutes=25), 12 * 3600 + 35 * 60)  # rest of day

# ── 5 days ago: a 2-day trip to Dublin via "Stansted" ──
trip_start = today_start - timedelta(days=5)
add_stay(*HOME, trip_start, 6 * 3600)
add_travel(*HOME, *STANSTED, trip_start + timedelta(hours=6), 70 * 60, n=6)  # drive to airport
add_travel(*STANSTED, *DUBLIN_HOTEL, trip_start + timedelta(hours=7, minutes=10), 55 * 60, n=4)  # "flight"
add_stay(*DUBLIN_HOTEL, trip_start + timedelta(hours=8, minutes=5), 14 * 3600)  # hotel, evening/night
add_travel(*DUBLIN_HOTEL, *DUBLIN_RESTAURANT, trip_start + timedelta(days=1, hours=6), 15 * 60, n=3)
add_stay(*DUBLIN_RESTAURANT, trip_start + timedelta(days=1, hours=6, minutes=15), 90 * 60)
add_travel(*DUBLIN_RESTAURANT, *DUBLIN_HOTEL, trip_start + timedelta(days=1, hours=7, minutes=45), 15 * 60, n=3)
add_stay(*DUBLIN_HOTEL, trip_start + timedelta(days=1, hours=8), 30 * 3600)  # rest of day 2 + into day 3 morning
add_travel(*DUBLIN_HOTEL, *STANSTED, trip_start + timedelta(days=2, hours=14), 55 * 60, n=4)  # flight back
add_travel(*STANSTED, *HOME, trip_start + timedelta(days=2, hours=15), 70 * 60, n=6)
add_stay(*HOME, trip_start + timedelta(days=2, hours=16, minutes=10), 6 * 3600)

points.sort(key=lambda p: p["tst"])

print(f"Posting {len(points)} points to {BASE_URL}/api/owntracks ...")
with httpx.Client(auth=AUTH, timeout=10.0) as client:
    for p in points:
        resp = client.post(f"{BASE_URL}/api/owntracks", json={"_type": "location", "tid": "RW", **p})
        if resp.status_code != 200:
            print(f"  ! {resp.status_code} for tst={p['tst']}: {resp.text}")
print("Done. Set home location via PUT /api/settings so trip grouping kicks in, e.g.:")
print(f'  curl -X PUT {BASE_URL}/api/settings -H "Content-Type: application/json" '
      f'-d \'{{"home_lat": {HOME[0]}, "home_lon": {HOME[1]}}}\'')
