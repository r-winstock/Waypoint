# Deploy — Waypoint on rowan

Self-hosted personal Timeline replacement: FastAPI + SQLite backend, Alpine.js/
Tailwind frontend, OwnTracks as the location-collection client.

| Setting | Value |
|---|---|
| Target host | **rowan** (192.168.1.70), Ubuntu 24.04, Docker + Portainer |
| Source | [github.com/r-winstock/Waypoint](https://github.com/r-winstock/Waypoint) (private) -> `~/code/waypoint` on rowan |
| Image | `waypoint:latest` (built on rowan, no registry) |
| Deploy | Portainer **stack** `waypoint` |
| Network | `Macvlan` (external), static IP **192.168.0.228** (confirm free before first deploy) |
| Reverse proxy | **NPM** (192.168.0.250) - proxy host `waypoint.home.lan` -> `http://192.168.0.228:8000` |
| DNS | **waypoint.home.lan** -> **192.168.0.250** (NPM, not the container) - Technitium `home.lan` zone |
| Data | `/opt/dockerData/waypoint/data/` on rowan (SQLite file) |
| Restart policy | `"no"` (house standard - will **not** auto-start after a reboot) |

> **Access pattern (house standard):** the container sits on the Macvlan at
> its own IP:port, and NPM fronts a clean hostname. DNS points at NPM, never
> at the container's own IP.

Every command below is labelled with the host it runs on. Run the build
inside a **tmux** session on rowan so a dropped connection doesn't kill it.

---

## 1. Get the code onto rowan

- **[rowan]**, first time only:
  ```bash
  mkdir -p ~/code && cd ~/code
  git clone git@github.com:r-winstock/Waypoint.git waypoint
  ```
- **[rowan]**, subsequent updates:
  ```bash
  cd ~/code/waypoint && git pull
  ```

## 2. Provision the data directory (one-time; safe to re-run)

- **[rowan]**:
  ```bash
  sudo mkdir -p /opt/dockerData/waypoint/data
  ```

## 3. Build the image

- **[rowan]** inside tmux:
  ```bash
  tmux new -s waypoint-build
  cd ~/code/waypoint
  sudo docker build -t waypoint:latest .
  ```
  This only builds the image - it does not touch any running container.
  (Needs `sudo`: this account isn't in the `docker` group.)

## 4. Deploy - via Portainer, not the shell

- **[browser]** Portainer -> **Stacks** -> create (or open) **`waypoint`**.
- Paste the contents of `stack.yml` and **update the stack**.
- ⚠️ **Gotcha:** `stack.yml` references `image: waypoint:latest` - a static
  tag with no registry. If you rebuilt the image (step 3) but the stack
  config text is unchanged, Portainer may not notice the underlying image
  changed. Force it **from within Portainer**: open the `waypoint` container
  -> **Recreate**. Never use "Re-pull image" - the image is locally built,
  there's no registry to pull from.

## 5. NPM proxy host

- **[NPM UI, 192.168.0.250]** Hosts -> Proxy Hosts -> Add:
  - Domain `waypoint.home.lan` - Scheme `http` - Forward Hostname/IP
    `192.168.0.228` - Forward Port `8000`
  - Websockets Support ON (not currently used, but harmless) - Save.

## 6. DNS

- **[Technitium, 192.168.1.70]** `home.lan` zone -> add A record
  **waypoint -> 192.168.0.250** (NPM, **not** the container).

## 7. Verify

rowan **cannot** reach its own macvlan IPs, so check from inside the
container or from another host:

- **[rowan]**:
  ```bash
  sudo docker logs --tail 50 waypoint
  sudo docker exec waypoint python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/healthz').read())"
  ```
- **[any other host / browser]** open **http://waypoint.home.lan** - the
  dashboard should load with the six tabs (Day/Trips/Insights/Places/
  Cities/World).

## 8. Configure OwnTracks (LAN-only sync)

On the phone, OwnTracks app settings:
- Mode: **HTTP**
- URL: `http://waypoint.home.lan/api/owntracks` (or the direct macvlan
  IP:port if the phone can't resolve `.home.lan` off Wi-Fi)
- Basic auth: username/password from `/api/settings` (defaults to
  `waypoint`/`waypoint` - change these before relying on it)
- Because sync is LAN-only by design, OwnTracks queues points while away and
  uploads the backlog once the phone rejoins home Wi-Fi.

Then set the home location so trip-grouping works:
```bash
curl -X PUT http://waypoint.home.lan/api/settings -H "Content-Type: application/json" \
  -d '{"home_lat": <lat>, "home_lon": <lon>, "home_radius_m": 500}'
```

---

## Updating later

- **[windows/dev]** push to GitHub.
- **[rowan]** `cd ~/code/waypoint && git pull && sudo docker build -t waypoint:latest .`
- **[Portainer]** open the `waypoint` container -> **Recreate**.

## Notes

- Background processing (stay-point clustering, trip segmentation, reverse
  geocoding) runs every 2 minutes via an in-process APScheduler job - no
  separate worker container. `POST /api/process` triggers it immediately
  if you don't want to wait.
- Reverse geocoding calls the public Nominatim API, self-throttled to its
  1 req/sec usage policy, with results cached indefinitely in the `places`
  table - repeat visits to the same spot never re-query it.
- **Optional:** `GOOGLE_PLACES_API_KEY` enriches new places with Google's own
  name/category data and powers a second "nearby alternatives" list in the
  "Fix this place" modal. Degrades to Nominatim-only if unset.
- **Optional:** `PHOTOPRISM_URL` (e.g. `http://photoprism.home.lan`) and
  `PHOTOPRISM_TOKEN` (an app password, Settings -> Apps and Devices in
  PhotoPrism) add a real-photo gallery to Day/Trip/Place detail views,
  sourced from your own PhotoPrism library rather than Wikipedia's stock
  photos. Degrades to no gallery (Wikipedia card photos are unaffected) if
  unset.
