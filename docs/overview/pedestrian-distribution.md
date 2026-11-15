# Pedestrian distribution around the UPC Lyft zone

Prepared November 14, 2026. This is an evidence inventory and modeling design,
not an estimated full-zone pedestrian distribution.

## What is now in the demo

The Campus activity workspace preloads historical reports, seasonal count
outlooks, a walking-network snapshot, and ambassador coverage scenarios. It
also includes a Satellite / aerial basemap and source-linked pedestrian-survey
markers. Selecting a different departure time changes the outlook, route
comparison, and ambassador scenario locally. It does not change the acquisition
date of the imagery or the observation period of a pedestrian survey.

The full displayed area is the current published **UPC** Lyft-zone approximation
in `docs/upc-lyft-boundary.geojson`: approximately 8.8 square kilometers. This
does not include the separate HSC area and is not an official ride-eligibility
geofence.

## Verified pedestrian observations

We inspected all 82 rows of the public 2025 LADOT aggregate survey table using
only corridor names and pedestrian totals. Two corridors could be matched to
named OSM intersections inside the study polygon:

- **Figueroa Street between 30th and 31st Streets:** 1,354 pedestrians across
  sampled periods; 1,063 in weekday periods and 291 in weekend periods.
- **Jefferson Boulevard between Vermont Avenue and Catalina Street:** 870
  pedestrians across sampled periods; 713 in weekday periods and 157 in weekend
  periods.

These are survey-period counts, not pedestrians per day, unique individuals,
or hour-by-hour estimates. The dataset describes weekday windows of 07:00–10:00
and 15:00–18:00 and weekend windows of 11:00–13:00. It does not supply exact
site observation dates and the underlying hourly records in this aggregate
table. A difference between the totals is not proof of a general traffic ratio
between the two streets. Differences in observation conditions can matter.

Sources: [2025 LADOT dataset](https://data.lacity.org/d/hiwe-xzrt),
[public dataset metadata](https://catalog.data.gov/dataset/2025-walk-bike-count-data).

The map markers are approximate corridor midpoints inferred from OSM road
intersections. They are not verified sensor coordinates. The matching process
does not establish that only two pedestrian count sites exist in the zone:
permanent counters, historical surveys, and unmatched names may provide more.

The reproducible preparer is `scripts/data/prepare_pedestrian_evidence.py`.
Its ignored local output, `data/activity/pedestrian-evidence.json`, records
selected fields, source URL, retrieval time, source-response SHA-256, totals,
geographic method, and limitations. No gender or other demographic fields are
requested or retained by that preparer.

## Satellite and aerial imagery

The UI offers [Esri World Imagery](https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer).
It is a mosaic of satellite and aerial sources. Current service attribution is
Esri, Vantor, Earthstar Geographics, and the GIS User Community. Collection dates
and resolutions vary by location and zoom. The UI does not claim a single
acquisition date for the whole zone or synchronize imagery to departure time.

Imagery is useful for interpreting paths, building footprints, tree cover,
barriers, and changes in the built environment. It is not used to detect people
or to produce historical pedestrian counts in this implementation. Small
objects, shadows, trees, occlusion, varying viewpoints, and sparse sampling
would require independent validation before any people-count inference.

The imagery tiles are fetched online when that basemap is enabled. All incident,
route-cost, pedestrian-evidence, and staffing analysis is precomputed/preloaded.
This is not an offline basemap downloader; no bulk tile archive or imagery
redistribution is included. Provider availability and terms still apply.

Esri documents [imagery citation metadata](https://doc.arcgis.com/en/data-appliance/latest/imagery-elevation/world-imagery.htm)
for checking source and collection information. We have not established a
ten-year, hourly USC imagery archive. Free Sentinel-2's finest bands are 10 m;
SkySat products sampled at 50 cm still do not promise historical hourly coverage.
See [ESA specifications](https://sentinels.copernicus.eu/documents/247904/685211/Sentinel-2_User_Handbook.pdf/8869acdf-fd84-43ec-ae8c-3e80a436a16c?t=1438278087000)
and [Planet specifications](https://docs.planet.com/data/imagery/skysat/).

## What a defensible flow model would require

The desired quantity is pedestrian **passages per hour** on a specified street
connection, separately from the probability that a person will experience harm.
Start with direct counts and record each site's observation hours, survey date,
counting direction, mode, measurement quality, and availability history.

Next inspect LADOT's [historic traffic counts](https://ladot.lacity.gov/residents/traffic-counts),
its [permanent counter program](https://ladot.lacity.gov/walkbikecount), and the
[California Active Transportation Data Portal](https://catdataportal.berkeley.edu/).
Citywide permanent counters exist; coverage within this zone and nighttime
observations still need verification. An institutional license for aggregate
mobility data could be another source, but no such access is assumed.

Given sufficient observations, fit a pooled count model:

\[
C_{j,t}\sim\operatorname{NegBinomial}(\Delta t\,p_j F_{j,t},\psi),
\qquad \log F_{s,t}=a_s+f(\text{hour},\text{weekday})+g(\text{season})+x_s^T\beta.
\]

Here C is an observed count, F pedestrian passages/hour, dt observed duration,
p a measurement factor only when calibrated, and x street/destination features.
Historical building uses, actual academic calendars, and transit access can
help estimate patterns. They cannot substitute for validation counts. Do not
estimate pedestrian exposure from incident reports themselves, which would
make an incident/exposure ratio circular.

Evaluate on withheld sites as well as withheld dates. Separate nighttime,
weekend, semester, and holiday performance; report predictive uncertainty and
coverage. Present unobserved street estimates as estimates and leave unsupported
periods explicitly unknown. Two aggregate corridors cannot identify a reliable
full-zone, ten-year hourly distribution on their own.

## How this affects routing and ambassador posts

**Pedestrian flow is excluded from current route and ambassador scores.** The
app compares modeled report activity along connected walking paths. A busier
street has not been assumed safer, and a quiet street has not been assumed
unsafe. Route suggestions are not validated personal-safety predictions.

Ambassador planning uses a limited staffing budget and non-overlapping coverage
of expected report activity within a 400 m network walk between representative
area connections. These are hypothetical posts, not actual DPS assignments.
The posts update whenever the selected weekday/time-window, report family, or
staff budget changes. The first outlook is seasonal: dates sharing a weekday
and three-hour window share the same prediction, without event-specific inputs.
Feasibility, current assignments, street conditions, gate access, and operational
judgment are still required.

If measured flow becomes sufficiently reliable, compare several models—with
and without pedestrian exposure—and validate their route ranking. All police
reports divided by pedestrian passages is not a victimization rate: the event
family, geography, time, and population must correspond. Carry uncertainty in
both quantities and suppress ratios with tiny or unsupported denominators.
Greater activity is contextual evidence, not an automatic safety multiplier.
