# Preloaded activity outlook, walking routes, and ambassador planning

Implemented locally on `codex/campus-activity-forecasting`. The app serves a
prepared public-data bundle through authenticated `GET /api/activity/exploration-bundle`.
No model fits or external routing requests run when a user changes time or asks
for a route. Historical report detail is included in the same bundle.

## Prepare the data host

Keep the existing `data/activity/activity.sqlite` and `metadata.json` together.
From the repository root:

```sh
.venv/bin/python -m scripts.data.download_forecast_streets
.venv/bin/python -m scripts.data.prepare_pedestrian_evidence
.venv/bin/python -m scripts.build_activity_forecast
```

The first two commands fetch bounded public OSM geometry and non-demographic
LADOT survey totals. The third is offline: it verifies input hashes, builds a
full-zone grid and walking graph, fits/evaluates count-model candidates,
precomputes all weekday/three-hour predictions and staffing scenarios, and
atomically replaces `data/activity/forecast-bundle.json`. It also writes
`forecast-model.json` and `forecast-evaluation.json` for reproducibility.

Copy the prepared bundle to the same relative path on an existing backend to
enable the new UI, or restore the checksummed public snapshot included under
`public-data/activity` with `python3 -m scripts.data.activity_snapshot restore`.
The private working `data/` directory remains ignored.
If preparation is missing, the UI reports that explicitly; it does not fabricate
predictions. Default deployment has not been changed by this local implementation.

## Scope and math

The current published UPC boundary approximation is partitioned into 172 clipped
250 m grid cells, totaling approximately 8.784 km². Cells on the diagonal edge
are clipped to the polygon. Empty cells remain represented and borrow strength
from surrounding areas. This is the UPC zone, not the separate HSC service area.

The implemented first model is a pooled seasonal empirical-Bayes count model,
not the proposed full log-linear hierarchy from the earlier design document.
Weighted event counts and zero-inclusive observation-day denominators estimate
weekday × three-hour rates. A 200 m Gaussian spatial kernel preserves total
mass within the boundary. Area-weighted spatial priors and temporal pooling
prevent fitting an independent sparse series for every street-hour.

Conditional Gamma-Poisson moments produce approximate negative-binomial count
distributions and central 80% predictive intervals. Power-likelihood recency
weights and empirical pooling mean these are not exact fully Bayesian calibrated
intervals. Small cell/window event counts produce highly conservative aggregate
coverage dominated by zero-event windows; this does not validate event recall.

Training uses a ten-year window where available, excludes occurrence dates in
the last 30 days before each cutoff, and requires a report date available by the
cutoff. Missing report dates are excluded. Original offense labels determine
person-directed vs property/vehicle families; financial-only and administrative-
only reports are excluded. Multi-family reports count once per family and must
not be summed across families. Person-directed reports include contexts that
cannot be established as outdoor or relevant to passing pedestrians.

Final training cutoff: November 14, 2026; mature occurrence history through
October 15. Source occurrence data extends through October 31. The seasonal
lookup supports the UI's next-seven-day selection, including exact departure
minutes for route traversal. Times within a three-hour window share a forecast;
dates sharing a weekday share the same first-model outlook. No game-day,
holiday, weather, or live-event adjustments are invented.

## Retrospective results on the saved snapshot

Candidate selection uses mean negative predictive log score on seven-day windows
after September 1, 2025; January 1, 2026; and April 1, 2026. Compared candidates:
static spatial density, recent seasonal, two-year-half-life seasonal, and a
modern-regime-only seasonal fit. The recent seasonal candidate won validation.

Untouched final periods start after August 1 and September 1, 2026. Their
report-family occurrence totals are 75 and 95. The seasonal candidate's mean
negative log scores are 0.02557 and 0.02972, versus 0.02564 and 0.03001 for the
static baseline. The improvement is modest.

Hotspot capture within a fixed 20% study-area budget is **33.3% and 45.3%** for
the seasonal model, versus **34.7% and 49.5%** for the static comparator. Thus
better count scores did not establish better hotspot capture. Report this mixed
result; no production route-safety or ambassador-effectiveness improvement has
been measured. A revised snapshot cannot recreate actual historical publication
availability, so validation is retrospective and subject to revision leakage.

## Map and routing

Orange street corridors inherit nearby grid estimates; they are not independent
street-level predictions. Thresholds are fixed across the reference week using
area-weighted forecast-density quantiles, rather than reassigning the top decile
at every time change. Sparse direct history has dashed cell borders. Click an
area for counts, probability of at least one report, approximate count interval,
and supporting history. These probabilities describe area reports, not harm to
a traveler.

The full-zone OSM extract is clipped to the boundary. Private/no-foot access,
motorways, indoor areas, construction and proposed ways are excluded. Original
OSM node identities preserve topology across bridges and coincident lines.
Routing snaps to the largest connected component only within 100 m; it rejects
outside-zone endpoints and does not invent straight-line walking connections.
Live restrictions, gates, crossings, lighting, and accessibility are unverified.

The direct route minimizes network distance at an assumed 80 m/min pace.
Several nonnegative departure-time activity-cost candidates are generated and
then scored using estimated traversal times. The alternative must respect the
selected detour budget and lower the report-activity index. This heuristic does
not guarantee global optimality for time-varying costs. Percent changes compare
an index, never a probability of being safer. Endpoint snap distances are
reported and their off-network walk is excluded from assessment.

## Ambassador scenarios, imagery, and pedestrian evidence

All staffing scenarios for 1–8 ambassadors are precomputed for each family and
weekday/time window. Greedy maximum coverage selects the next candidate with
the greatest uncovered expected report count within a 400 m network walk of
representative grid connections. Overlap does not create duplicate benefit.
Blue numbered posts and approximate covered areas appear when enabled. They
are planning suggestions, not real DPS assignments or evidence of effectiveness.

Satellite/aerial tiles are optional online geographic context with attribution;
the analysis remains preloaded. Two 2025 LADOT survey corridors are displayed
with sampled-period counts. No full-zone hourly pedestrian model is claimed or
used in routing. See [pedestrian distribution and imagery documentation](pedestrian-distribution.md).

## Verification

Run `pytest -q`, `npm run test:activity`, and `npm run build --prefix apps/web`.
Forecast tests check polygon coverage, clipping, taxonomy, maturity/report-date
leakage guards, uncertainty arithmetic, authentication, and non-overlapping
staff coverage. Routing tests check detour constraints, disconnected/outside-zone
points, and midnight slot changes. A local read-only browser preview verifies
map controls, imagery, route selection, history, and staffing interactions.
