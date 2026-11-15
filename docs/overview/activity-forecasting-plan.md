# Campus activity forecasting and pedestrian flow: proposed design

Status: original design discussion, November 14, 2026. Branch: `codex/campus-activity-forecasting`.
A first preloaded seasonal outlook, local route comparison, imagery context, and
ambassador planning implementation now exists locally. See the
[implemented method and measured limitations](activity-forecasting.md). A full
pedestrian-flow model is not implemented; current survey evidence is documented
in [pedestrian distribution](pedestrian-distribution.md). Nothing has been deployed.

## What the existing data actually supports

The activity importer downloads structured public LAPD records. No language model
currently fits these records: the existing map queries SQLite and displays history.
Local files are `data/activity/activity.sqlite` (21,901,312 bytes), `metadata.json`,
selected-field compressed source pages, and several OSM extracts. Data is ignored
by Git. The importer, boundary, and documentation are versioned. There are no
`data/` commits in the locally available Git history; GitHub branch-head checking
confirmed the current source branch head. Do not force-add the whole data folder:
it also contains credentials, media, databases, and private operations elsewhere.

Reproduce the aggregate audit with:

```sh
.venv/bin/python -m scripts.audit_activity_forecasting
```

The ignored result is `data/activity/forecast-audit.json`. Its database SHA-256
matches the recorded download manifest. Findings from this exact snapshot:

- All history: 75,686 grouped reports, January 2010 through October 31, 2026.
- Latest ten complete years, November 1, 2016–October 31, 2026: 48,403 reports,
  1,533 distinct approximate coordinates, 3,652 calendar days.
- Latest 365 days: 5,265 reports at 629 coordinates, about 14.4 reports/day
  across the entire area. This is before filtering to relevant incident types.
- 38.7% of ten-year occurrence times land exactly on an hour; 963 are 00:00
  or 00:01. Rounding is not proof of an invalid timestamp, but minute-level
  forecasts would claim unsupported precision.
- Ten-year report delays: median 0 days, p90 8, p95 27, p99 273. There are
  210 missing report dates. The latest year's p95 is 14 days.
- All 2025–2026 records use NIBRS; older data uses a different reporting
  system, with a mixed transition during 2024. Raw year comparisons need care.
- The broad browsing taxonomy puts 1,518 ten-year identity-theft reports into
  “Theft or burglary.” It is unsuitable as a walking-risk label.
- In the latest year, 76 of 250 reports at 06:00–06:59 include a rail-transit
  trespass label. A count peak can reflect enforcement activity, not pedestrian
  victimization. Multi-offense labels can overlap; these are report counts.
- The normalized schema has coordinates but no verified street name/segment ID.
  Existing boundary-building OSM extracts contain 337 named highway ways;
  coverage, walkability, access restrictions, and connectivity are unvalidated.

The snapshot ends fourteen calendar days before the proposed demo date. “Now”
must mean a model prediction for the selected Los Angeles time, with the data
cutoff shown. It cannot mean currently observed incidents or live foot traffic.

## Product target

Forecast the distribution of future **reported activity counts** by area, time
window, and defensible incident family. Separately estimate pedestrian flow
where observations support it. Compare walking routes using these layers and
display both evidence strength and uncertainty. This does not estimate the
probability that a specific traveler will be harmed.

Start with approximately 200–300 m spatial units and three-hour windows. Tune
those choices in historical validation; do not fit an independent time series
for every street-hour-category. Use street highlights as a rendering of nearby
area estimates until street attribution has been validated. Approximate points
must not be treated as exact sidewalk locations or assigned across barriers.

## 1. Define and audit the numerator

Create a versioned crosswalk from original offense codes/descriptions to model
families, inspecting every offense in a grouped report. Preserve original
classifications and count a report once within each selected family; combined
views must deduplicate report IDs. Do not sum overlapping categories.

Separate person-directed reports, property/vehicle reports, financial/identity
reports, and administrative/enforcement activity. Location alone does not
establish that an event happened outdoors or involved a passing pedestrian.
Unknown context stays unknown. Show separate layers rather than inventing a
universal severity score. Retain the complete history for browsing.

Build a complete zero-inclusive space/time panel, with missing coverage marked
unknown. Harmonize sources and assess their overlap. Compare fits restricted to
the modern regime against fits borrowing older seasonal/spatial structure.
A source indicator cannot by itself identify a reporting change separately
from a real time trend when source and date are confounded.

## 2. Incident forecast math

First benchmark a smoothed recent seasonal count rate and a regularized Poisson
regression. Candidate: a hierarchical negative-binomial count regression:

\[
Y_{s,t,k}\sim NB(\mu_{s,t,k},\phi_k),\qquad
\operatorname{Var}(Y)=\mu+\mu^2/\phi_k
\]

\[
\log\mu_{s,t,k}=\log D_{s,t}+\alpha_k+u_{s,k}
+f_k(\text{hour},\text{weekday})+g_k(\text{season})+r_{k,t}
\]

Here s is an area, t a dated time window, k an incident family, and D is
observed duration (not pedestrian exposure). The spatial effects u partially
pool toward neighborhood/area effects; cyclic hour effects join 23:00 to 00:00;
hour-by-weekday effects distinguish Friday evening from Monday morning.
Use regularized slow trend r rather than an unconstrained linear extrapolation.
Only add academic-calendar/event covariates whose historical values and future
schedule are actually available. Do not invent game-day or class schedules.

Exponential training weights express the preference for recency:

\[
w_i=2^{-\mathrm{age}_i/H}
\]

Select half-life H from 6, 12, 24, and 36 months inside chronological validation,
using age relative to each training cutoff. Weight zero-event intervals too.
Compare with a recent-only model; ten years of history is optional evidence,
not a requirement to give obsolete patterns equal influence. Weighted likelihood
uncertainty requires calibration; do not read nominal intervals as guaranteed.

Under a fitted NB2 distribution, the conditional probability of at least one
report in that specified area/window is:

\[
P(Y\ge1)=1-\left(\frac{\phi}{\phi+\mu}\right)^{\phi}
\]

Integrate parameter uncertainty using model/posterior draws or a suitable
temporal-block bootstrap; then calibrate predictive intervals on holdouts.
An interval for the mean and a predictive interval for future counts are
different. The probability above is not a person's harm probability. Sparse
windows may correctly have a predictive interval starting at zero.

Plain ordinary least squares can predict negative counts and supplies the wrong
default noise model. A Markov chain or large neural model is not necessary for
this first dataset. NB is a candidate, not a predeclared winner: the latest
year's area-wide daily variance is about 20.0 versus mean 14.4, but that raw
comparison does not establish residual overdispersion after covariates.

## 3. Pedestrian flow: feasible, but not from an hourly satellite archive

Free Sentinel-2 imagery has finest bands at 10 m and a nominal five-day
two-satellite revisit. It cannot count individual walkers at hourly resolution.
Planet SkySat products are sampled at 50 cm and advertise up to ten daily
collection opportunities. That is not a promise of historical USC coverage at
those times. Occlusion, sample bias, and sparse acquisitions would still need
validation. Inspect an archive before claiming any available USC scenes.
Use imagery for changing buildings, land use, and environmental covariates;
do not reconstruct nonexistent observations with image generation/enhancement.

Better evidence sources, in priority order:

1. LADOT Walk & Bike Counts, NavigateLA historic surveys, and permanent counters.
   LADOT documents a count site on Figueroa between 30th and 31st Streets near
   USC, including a 2019 comparison. Its 2025 update mentions the same corridor.
   A direct read of the 2025 public dataset `hiwe-xzrt` verified the Figueroa
   30th–31st row: 1,354 sampled pedestrians, comprising 1,063 in weekday survey
   periods and 291 in weekend periods. These are survey-window totals, not daily
   totals. This published table supplies period aggregates, not an hourly history;
   retrieve original observation dates/hours before estimating hourly rates.
   Citywide permanent counters operate 24/7; USC-area counter coverage and exact
   downloadable hourly observations still need verification.
2. The California Active Transportation Data Portal, which succeeded SCAG's
   regional database. Check count locations, dates, modes, and hour coverage.
3. Licensed aggregate mobility data if available through USC or a provider.
   StreetLight documents pedestrian mode and day/hour aggregates. USC coverage,
   years, sample sufficiency, access, cost, and redistribution are unverified.
   Vendor estimates are not ground-truth pedestrian counts.
4. Walking-network features, destinations, transit schedules, public building
   uses, and verified academic calendars to interpolate between observations.
5. Prospectively collected anonymous line-crossing counts at authorized camera
   views to validate/refine the model. No identification is required; present
   observations cannot prove historical footfall.

Model count observations C at counter j over duration dt as:

\[
C_{j,t}\sim NB(\Delta t\,p_j F_{j,t},\psi),\quad
\log F_{s,t}=a_s+f(\text{hour},\text{weekday})+g(\text{season})+x_s^T\beta
\]

F is pedestrians/hour, x contains street/destination features, and p is a
measurement/detection factor only if it can be calibrated. Fix it or report its
assumption when it cannot be estimated. Pool sparse sites. A network/gravity
origin-destination assignment can supply a spatial prior, but sparse counters
cannot uniquely identify every origin-destination flow.

Validate on held-out counters and periods, especially nights and weekends.
Do not infer pedestrian demand from incident counts themselves: that would make
the later incident/exposure comparison circular. Reconstructed past traffic is
a model estimate with changing historical infrastructure and uncertainty, not
a recovered measurement. Ten-year hourly coverage is not established.

If we later compute reports per 10,000 estimated pedestrian passages, the
numerator must correspond to relevant events in the same space/time. All LAPD
reports divided by footfall is not a victimization rate. Carry uncertainty in
both forecasts and suppress unstable ratios at tiny/unknown denominators. Even
an exposure-normalized report index does not correct underreporting or establish
personal risk. Use that label until independently validated.

## 4. Validation before an impressive map

Use rolling forecast origins, e.g. several monthly cutoffs during 2025–2026,
with 1-day, 7-day, and month-ahead horizons. Select features/half-life/resolution
only within training/validation folds; retain a final untouched recent period.
Evaluate matured targets (initially with a 30–60 day buffer, plus sensitivity
to longer delays), and exclude missing/late report dates from the training
information set or flag them explicitly. A current revised snapshot still
cannot recreate historical publication availability, so call this retrospective
validation. Begin saving dated snapshots now for prospective evaluation.

Compare with static density, recent-only density, and smoothed same-weekday/
time baselines. Measure predictive log score, count error, interval coverage,
and fraction of held-out reports captured within a FIXED-area hotspot budget.
Check results by category, area, source regime, and sparse-support group.
Maintain an absolute intensity scale across times: recoloring the top decile at
every slider position would manufacture hotspots even during quiet windows.

For footfall, additionally hold out entire counter sites; evaluate observed
counts and interval coverage. If pedestrian evidence is insufficient, deliver
the incident forecast and keep flow estimates visibly experimental/separate.

## 5. Map and routing demo after design agreement

- Add History / Forecast / Pedestrian activity modes to the current MapLibre map.
- Select Now, Tonight, Tomorrow, or a custom Los Angeles departure date/time.
  Begin with a seven-day forecast horizon and honest three-hour resolution.
- Use orange street corridors for elevated modeled nearby report activity.
  Add a slow pulse for emphasis, an animation toggle, and reduced-motion support.
  Label “elevated forecast report activity,” not “a crime is happening here.”
- Clicking a corridor shows the forecast window, expected count/distribution,
  relative intensity, category mix, historical support, model version, and cutoff.
- Separately show measured vs estimated pedestrian flow, with an evidence legend.
  Density does not establish that a busy route is safer or an empty route unsafe.
- Build a full walking graph with crossings, access/gates, and usable paths;
  validate connectivity. Existing boundary extracts are not a routing engine.
- Compare shortest and lower-report-activity alternatives with a user-controlled
  detour budget. Integrate the forecast along each edge at estimated arrival time.

A first route objective is

\[
J(R)=\sum_{e\in R}\tau_e[1+\beta q_e(t_e)+\gamma u_e(t_e)]
\]

where tau is minutes, q a dimensionless reference-normalized report-density
index, and u a dimensionless uncertainty/coverage penalty. Keep scaling fixed
across time; do not multiply adjacent probabilities as if independent. The
result is a route-ranking score, not a journey harm probability. Unknown areas
must not acquire zero cost simply because no data are available.

## 6. Ambassadors and cameras are separate planning decisions

Use forecast demand, walking access, existing coverage, and a fixed staffing
budget in a maximum-coverage/location-allocation scenario. Show the incremental
coverage of each candidate ambassador position. If walking-demand estimates are
weak, present alternatives under several demand scenarios.

Camera siting additionally needs visibility, obstructions, installability,
existing equipment, and review requirements. Historical report density alone
does not establish camera effectiveness or an optimal location. Keep these
outputs as human-reviewed planning scenarios, distinct from public route advice.

## Implementation sequence and storage

1. Resolve taxonomy, source regimes, time quality, spatial attribution, and
   snapshot retention; inspect local pedestrian observations.
2. Implement and backtest incident baselines and the count-model candidate.
3. Add map time controls, probability/count detail, and fixed-scale street layers.
4. Fit/validate pedestrian flow only where data supports it; surface uncertainty.
5. Add walking-route comparison, then ambassador/camera coverage scenarios.

Fit offline on CPU and save a small versioned model/forecast bundle with hashes
and evaluation metadata. The FastAPI control plane serves forecast GeoJSON;
it need not use the video GPU. Add activity-specific modules/endpoints rather
than entangling forecasts with live video evidence scores.

For reproducibility, keep code and small aggregate audit/model manifests in Git.
Consider a checksummed, minimized public-data snapshot as a release asset after
reviewing source terms, rather than checking raw caches into application history.
No dataset or new branch has been pushed as part of this planning task.

## Sources consulted

- [Local source methodology](activity-data.md).
- [Stan NB2 distribution](https://mc-stan.org/docs/functions-reference/unbounded_discrete_distributions.html).
- [Forecasting evaluation and rolling origins](https://otexts.com/fpp3/toolbox.html).
- [ESA Sentinel-2 product resolution and revisit](https://sentinels.copernicus.eu/sentinel-data-access/sentinel-products/sentinel-2-data-products/collection-0-level-2a).
- [Planet SkySat specifications](https://docs.planet.com/data/imagery/skysat/).
- [LADOT count program](https://ladot.lacity.gov/walkbikecount).
- [LADOT historic traffic counts](https://ladot.lacity.gov/residents/traffic-counts).
- [LADOT 2019 report, MyFigueroa case study](https://ladot.lacity.gov/sites/default/files/documents/ladot2019walkandbikecount.pdf).
- [LADOT 2025 count announcement](https://ladot.lacity.gov/dotnews/weekly-update-october-01-2026).
- [LADOT 2025 count dataset](https://data.lacity.org/d/hiwe-xzrt), verified via
  `resource/hiwe-xzrt.json` filtered on `street_name` containing `FIGUEROA`.
- [SCAG and the statewide active transportation portal](https://www.scag.ca.gov/transportation).
- [StreetLight metric documentation](https://developer.streetlightdata.com/docs/available-metrics).
