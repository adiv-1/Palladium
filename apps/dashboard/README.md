# SafeSC 3D command dashboard

A full-screen 3D view of USC for the dispatcher. Live incident beams pulse until someone reviews them. Approving an incident sends the nearest officer, shown as an animated arc. Hexagon columns show history by hour of day, and camera cones show how much of campus is watched live and how much is AI triaged. Every layer is labelled Real, Demo, Simulated or Estimate.

## Run

```bash
cd apps/dashboard
npm install
npm run dev        # http://localhost:5174
```

Press **D**, or click **Stage demo incident**, to fire a staged event.

| Key | Action |
|---|---|
| D | Stage a demo incident |
| M | Switch map mode (Google 3D Tiles / MapLibre fallback) |

### Map modes

- **Google Photorealistic 3D Tiles:** set `VITE_GOOGLE_MAPS_API_KEY` in `apps/dashboard/.env` and enable the Map Tiles API for that key. Check pricing and quotas first; we have not verified them, and we have not tested this mode with a real key.
- **MapLibre fallback (default):** OpenFreeMap vector tiles with extruded OSM buildings in a dark style. It's free, needs no key and works on poor wifi.

### Connect to a live SafeSC

1. Run the SafeSC API on `:8000` (`STREAMING_MODEL=cascade` is recommended).
2. In the dashboard, click **Connect SafeSC** and enter the token from `data/app_token`.

Vite proxies `/api` and `/ws` to the API. Set `SAFESC_API` to point the proxy somewhere else. SafeSC sources carry no location, so live incidents are pinned to **simulated** camera positions.

## Data provenance

| Data | Source | Label on screen |
|---|---|---|
| Bike racks, buildings | OpenStreetMap via `scripts/build_osm_data.py` (© OpenStreetMap contributors, ODbL) | Real |
| Live incidents | Staged events, or a connected SafeSC | Demo / Live |
| Camera positions, officer roster | Invented; deliberately **not** real DPS camera locations | Simulated |
| History columns | Simulated until `public/data/history.json` exists | Simulated |
| Cost today | $0.60/h per L4 GPU at 40 cameras per GPU, from the feasibility brief; not measured | Estimate |

**Real history:** geocode the DPS daily crime log (hand-mapping the top ~30 locations is enough) and save it as `public/data/history.json`:

```json
[{"lat": 34.0224, "lng": -118.2910, "hour": 21, "type": "rack_activity"}]
```

`type` is one of `person_down`, `altercation` or `rack_activity`; `hour` is 0 to 23.

## Demo script for judges

1. **Idle:** the camera slowly orbits campus at dusk.
2. **Stage an incident:** press D. A beam appears and the alert card slides in.
3. **Fly-in:** the camera drops to the building in about 2 seconds, and the card shows evidence and the recommended responder.
4. **Approve:** the beam turns steady.
5. **Dispatch:** the camera pulls back to show the arc from the officer to the incident, and the officer ping appears. Then it returns to orbit.
6. **History:** drag the hour slider to 9 PM and watch the columns rise around the bike racks.

## Known limits

- Google 3D Tiles are not desaturated, and that mode is untested with a real key.
- There is no officer phone app; the ping is an on-screen toast.
- History is simulated until a geocoded DPS log is added.
