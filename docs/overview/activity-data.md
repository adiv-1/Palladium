# Public activity snapshot

The Campus activity tab uses official structured LAPD public records, not complete police narratives, Citizen posts, or camera judgments. The snapshot downloaded November 14, 2026 contains **75,686 grouped reports**, occurrence dates January 1, 2010 through October 31, 2026. The normalized SQLite file is **21,901,312 bytes**. Selected raw pages, request manifests, and SHA-256 hashes remain locally under ignored `data/activity/`; the dashboard server has only the minimized database and metadata.

## Sources and geographic scope

- [LAPD 2010–2019](https://data.lacity.org/d/63jg-8b9z): 54,595 selected rows in the download bounding box.
- [LAPD 2020–2024](https://data.lacity.org/d/2nrs-mtv8): 28,984 selected rows.
- [LAPD NIBRS offenses](https://data.lacity.org/d/k7nn-b2ep): 15,729 selected rows.
- [USC Lyft program](https://transnet.usc.edu/index.php/how-to-use-lyft/) and its [published UPC map](https://transnet.usc.edu/wp-content/uploads/2024/01/UPC-Boundaries.pdf).

The current University Park boundary follows Normandie, Washington's straight and diagonal portions, Main, and Martin Luther King Jr. Boulevard. `docs/upc-lyft-boundary.geojson` digitizes the published shape using street-intersection coordinates from OpenStreetMap (© OpenStreetMap contributors, ODbL). It is not the official proprietary Lyft geofence; check Lyft for ride eligibility. It excludes HSC and is applied consistently to historical years, without asserting those years had identical service areas. Approximate block coordinates can fall on the wrong side of an edge.

Download requests select occurrence/report dates, listed occurrence time, public case/offense identifiers, classifications, and approximate coordinates. Victim age, sex, descent, names, exact addresses, and suspect fields are not requested. Raw selected-field pages use gzip, cache keys include the request definition, and the metadata manifest records query fields, predicates, page hashes, row counts and fetch date.

22,247 downloaded rows were outside the polygon. Another 1,375 rows were merged by exact normalized case number. NIBRS can list several offenses per case; the output retains all classifications and source dataset links. New-system records take precedence for shared case numbers. Prefixes such as `C` are preserved. Different case-number conventions or different agencies may still cause duplicates; no fuzzy merging is attempted. The archive is not a count of unique people or victims. A broad display category is for browsing, not severity ranking.

## What the interface means

Date filters include both endpoints and use **occurrence date**, without converting Los Angeles calendar dates to UTC. Small fixed-size circles use pale-to-dark orange based only on recency within that selected interval; newest reports draw above older ones. No severity radius, heatmap, street safety score, or unsolicited public alert is produced. The list and category filters also work without interpreting map colors. Clicking opens templated source-grounded text, avoiding generated narratives or invented details. Reports sharing an approximate location can be selected individually.

The recent end of this snapshot stops October 31 even though download occurred November 14. Empty recent periods do not establish no incidents occurred. Incidents may be unreported, delayed, reclassified, approximately located, or erroneous. A report is not a finding of guilt. Midnight occurrence times can be uncertain or unspecified.

## Refresh and deployment

From the project root, run `uv run python scripts/data/download_activity.py`. It downloads bounded pages, validates/parses them, clips the polygon, groups cases, then atomically replaces the normalized database. Downloading does not change live video records. Keep `activity.sqlite` and `metadata.json` together when copying to `data/activity/` on a worker. API endpoints require the same reviewer authentication as video records. Map tiles are fetched normally from OpenStreetMap with attribution; do not bulk prefetch or use this prototype as an offline tile service.

## Forecasting boundary

The Historical reports view provides observed history. A separately prepared
[seasonal outlook](activity-forecasting.md) now adds forecast exploration,
walking comparisons, and hypothetical ambassador coverage. LAPD's transition
to NIBRS and reporting differences prevent naive year-to-year count comparisons.
The first outlook compares source-regime candidates and preserves a mature
training cutoff. A current revised dataset is not a historical as-of snapshot:
filtering by report date reduces leakage but cannot reconstruct when each record
originally became public or how it was later revised. Historical counts lack
pedestrian exposure and cannot measure an individual's risk. DPS logs may be a
later source, with separate provenance and duplicate reconciliation.
