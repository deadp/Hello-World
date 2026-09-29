# Wellington cycle gap analysis: improvement plan

**Status (29 Sep 2026): phases 0–2 are done (version 3).** The results are in the README section
"Where should new cycle connections go?".

Changes from what's planned below:
- Calibration compares counts per sensor, because one camera covers a road and its path.
- The best fit is stress factors 2/3 and a climb weight of 20.
- Agreement with the counts rose from 0.44 to 0.55.

Phases 3–5 are next.

This plan comes from four research reviews carried out on 2026-09-29, each checked against the current code:

- cycleway classification from OSM;
- demand modelling;
- New Zealand data sources;
- network prioritisation.

The current pipeline is `fetch_cycling.py`, `cycle_network.py`, `cycle_model.py`, `cycle_gaps.py` and `build_cycle_map.py`. The phases below are in the recommended order. Each lists its effort and what it should change.

## What the current model gets wrong or leaves out

| Area | Now | Problem |
|---|---|---|
| Uptake model | PCT Go Dutch 2017 coefficients plus e-bike terms | The e-bike terms belong to the 2020 model, which centres gradient at 0.78%. The production PCT uses the 2020 model. |
| Current cycling | Ignored | Go Dutch can come out below 2023 actual cycling. Karori East to the CBD is 11.6% actual against 11% Go Dutch. Scotland's NPT uses max(Go Dutch, observed). |
| Route gradient | (climb + descent) / length, from 5 m LiDAR sampled every 10 m | Same definition as PCT, but unsmoothed. It may read hillier than PCT's smoothed CycleStreets profiles. |
| Speed limits | OSM `maxspeed`; 17% of road length untagged and defaulted to 50 | NZTA's register has the current legal limits, including the 2024 rule changes. |
| Traffic stress | Four crude rules | 30 km/h streets with 2–4k vehicles/day are rated as needing protection. Sharrows, buffers, lane counts, contraflow and crossings are ignored. |
| Direction | One facility per street segment (the best side) | Transitional cycleways are often protected uphill and sharrow downhill, so the model overstates downhill protection. |
| Facility types | Five buckets | Tracks, shared paths, buffered lanes, bus lanes and sharrows are not told apart. CyclOSM draws finer distinctions from the same OSM data. |
| Validation | None | The council publishes hourly cyclist counts from 25+ cycle countlines. |
| Trips | Commute and education only (~20% of all trips) | Shopping, leisure and visiting are missing. The school model is not applied. |
| Ranking | Potential trips × length on busy unprotected edges | It favours long suburban roads. It has no network effect, safety, cost or treatment type. |

## Phase 0: quick fixes to the uptake model (half a day)

1. **Switch to the PCT 2020 model** (`uptake_pct_godutch_2020`, from npct/pct-scripts `05.1_commute_scenario_building.do`). Centred gradient g_c = g − 0.78.
   - Base: logit = −4.018 − 0.6369d + 1.988√d + 0.008775d² − 0.2555g_c + 0.02006·d·g_c − 0.1234·√d·g_c
   - Go Dutch adds +2.550 − 0.08036d.
   - E-bike adds +0.05509d − 0.000295d² + 0.1812g_c on top of Go Dutch.
   - Cap distance at 30 km. Keep the 2017 model as a sensitivity.
2. **Floor every scenario at observed cycling:** potential = max(p·trips, census cyclists), as NPT does. Also add a Government Target-style scenario: observed plus the base-model increment for non-cyclists.
3. **Check the gradient measure.** Smooth elevation over about 50 m before differencing, then compare route gradients with a few CycleStreets routes (Karori, Island Bay, Kelburn to the CBD).
4. **Suppressed census cells:** drop pairs under about 20 trips from any model fitting. Keep the current rule (−999 counts as 3 total trips and 0 cyclists) only for routing.

Expected effect: more potential on short flat trips, less on steep ones, and the western and southern commuter routes no longer below today's levels. The gap ranking should barely change.

## Phase 1: better network inputs (1–2 days)

All sources below are public, need no key, and were tested on 2026-09-29.

1. **Speed limits from NZTA's National Speed Limit Register.** CC BY 4.0, refreshed nightly.
   - Source: `https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/SpeedLimitZoneFull__View/FeatureServer/0`
   - Filter to current records: `whenEffective<=CURRENT_TIMESTAMP AND (whenIneffective IS NULL OR whenIneffective>CURRENT_TIMESTAMP)` and `rcaZoneReferenceName IN ('Wellington City','State Highways')`. This returns 419 zones.
   - The zones are polygons. Overlay each edge's midpoint and take the minimum permanent limit. Keep school and variable zones as a flag.
   - This replaces OSM `maxspeed`.
2. **Traffic volumes.**
   - State highway AADT from NZTA monitoring sites (`Assets_SHTrafficMonitoringSites/FeatureServer/0`), assigned to the nearest state highway ways. This fills SH1/SH2, which currently have no ADT.
   - Where there is no count, use osmactive's defaults by road class: residential, unclassified and service 500; tertiary 3,000; secondary 5,000; primary 6,000; trunk 8,000.
3. **Facility levels.** Replace `facility()` with ten classes, adapted from CyclOSM, osmactive and CQI; the first rule that matches wins:
   - track: a cycleway not shared with pedestrians, `cycleway*=track`, or the council layer's Separated/Barrier;
   - protected lane: `cycleway*=lane` with `cycleway*:separation`;
   - segregated path: a path with `segregated=yes`;
   - shared path;
   - shared footway;
   - buffered lane;
   - painted lane;
   - bus lane;
   - sharrow (treated as mixed traffic);
   - mixed traffic.

   Handle per-side tags (`:left`, `:right`, `:both`) and contraflow (`oneway:bicycle=no`, `opposite_*`).

   Of the 922 OSM cycleways, 314 have no `foot` or `segregated` tag. Default them to shared path, since most NZ cycleways are legally shared, and flag them on the map.
4. **Traffic stress as a speed × volume × road type table**, following Furth LTS v2 (2017), checked against UK LTN 1/20 Figure 4.1, the Dutch CROW design manual and Auckland Transport's design code.
   - **Mixed traffic.** ADT bands: 0–750, 751–1,500, 1,501–3,000, over 3,000. One-way streets count at 1.5 × ADT.

     | Speed limit | No centre line | Centre line |
     |---|---|---|
     | ≤30 km/h | 1/1/2/2 | 1/2/2/3 |
     | 40 km/h | 1/1/2/3 | 1/2/3/3 |
     | 50 km/h | 2/2/2/3 | 2/2/3/3 |
     | 60 km/h | 2/3/3/3 | 2/3/3/3 |
     | ≥70 km/h | 3/3/4/4 | 3/3/4/4 |

   - **30 km/h at 3,000–5,000 vehicles/day:** level 2 with no centre line, level 3 with one.
   - **Painted and buffered lanes:**
     - up to 40 km/h: level 1–2;
     - 50 km/h: level 2, or level 3 if ADT is over 6,000 or there are 3+ lanes each way;
     - 60 km/h: level 3;
     - 70 km/h or more: level 4.
   - **Bus lanes:** rated as painted lanes, with a minimum of level 3 at 50 km/h or more.
   - **Parameters:** thresholds live in a table, not code, so the NZTA Cycling Network Guidance chart can be added once someone can read it (the NZTA site blocks scripted access).
5. **Crossings and junctions.**
   - Also fetch OSM `node[highway=traffic_signals]`, `node[crossing]` and `node[barrier=cycle_barrier]`.
   - Signalised crossings are level 2. Zebra or marked crossings of roads up to 50 km/h and 8,000 ADT are level 2. Otherwise a crossing takes the level of the road being crossed.
   - At unsignalised junctions, approaches take the highest level of the streets they cross (Conveyal's rule).
   - Cycle barriers add a time penalty.
6. **Rate facilities by direction.**
   - WCC's transitional programme often protects the uphill side with flexible posts or kerb separators and leaves a sharrow or nothing downhill.
   - OSM has 562 road ways with different facilities on each side, 28 of them a separate cycleway on one side and a sharrow on the other. Only 16 ways tag the separator type (`cycleway:*:separation=flex_post`).
   - The current `facility()` takes the best side for the whole edge, so it overstates downhill protection. Instead, set facility and traffic stress on each directed arc:
     - on two-way ways, `cycleway:left` applies to the forward direction (the direction the way is drawn) because NZ drives on the left, and `cycleway:right` to the reverse;
     - handle `oneway`, `oneway:bicycle=no` and `opposite_*` for contraflow.
   - Treat flex posts, WCC "Barrier" and kerb separators as protected lanes.
   - Downhill mixed traffic: riders descending go close to traffic speed, so on grades over about 4% use the next-lower speed band. Floor this at level 2 at 30 km/h, and don't apply it above 50 km/h.
   - Make this a parameter and test it against directional counts (Phase 2).
   - In the gap ranking, a street protected uphill with a low-stress descent counts as served. Flag a street as a directional gap when only one direction is low stress.
7. **Signed routes and plan status.**
   - Add the OSM cycle route relations as a map layer: national (Tour Aotearoa, Hutt River Trail), regional (Great Harbour Way, Te Ara Tupua, Ngauranga Gorge) and about 40 local routes.
   - Relabel the council plan's `LGWM` stage as "unfunded (ex-LGWM)". LGWM was disestablished in December 2023.
   - Note which routes are built since 2022, including Te Ara Tupua, which opened on 16 May 2026.

Expected effect: busy 30/40 km/h central city streets such as Cuba Street, Lambton Quay and Manners Street partly drop out of the gap list. State highways and 50 km/h arterials get correct stress levels, and crossings of arterials appear as gaps in their own right.

## Phase 2: validation against counts (1–2 days)

1. **Cyclist counts.** Hourly cyclist counts from WCC's VivaCity sensors: 411 countlines from November 2023, refreshed daily, in a public S3 bucket.
   - Bucket: `https://gis-snowflake-opendata-public-wcc-arcgis-prod.s3.ap-southeast-2.amazonaws.com/transport_sensors/`
   - Files: `countline_meta_info.csv` (locations), `countline_mobility_cyclist.csv` (counts) and `viewpoint_availability_daily.csv` (sensor uptime).
   - Compute the average weekday during school term, weighted by uptime, and the 7–9am inbound count, for each countline.
2. **Compare with the model.** Snap countlines to network edges and compare with the census cyclists routed in the model, keeping direction.
   - Fit counts = k × modelled.
   - Report R², GEH and mean absolute percentage error, with residuals by corridor.
   - Expect k of about 1.5–3, because counts include trips other than commuting. k becomes the local factor for scaling commute flows to all-day flows.
3. **First comparison with the model (29 Sep 2026).**
   - **Data:** `countline_mobility_cyclist.csv` has 6.1M hourly rows from 406 countlines, 2023-11-03 to 2026-09-27. Cyclists are counted at every countline, not only the 25 cycle-primary ones.
   - **Method:** average weekday since Sep 2025, using days with at least 22 reporting hours and countlines with at least 40 such days.
   - **Busiest sites (cyclists per average weekday):**
     - waterfront (Commonwealth Walkway / Ara Moana): about 1,500–1,650;
     - Cambridge Terrace cyclepath: 896;
     - Hutt Road cyclepath: 800;
     - Riddiford Street: 566;
     - Tasman Street: 560;
     - Chaytor Street: 519.
   - **Match with the model:** snapping 37 countlines (20+ cyclists a weekday) to model edges gives a weak log correlation of **0.30**. Counted cyclists are 1.28× the modelled census cyclists overall.
   - **Where the gaps are:**
     - **Route choice.** Cambridge Terrace has 896 counted against 17 modelled, and Tasman Street 560 against 16. Real riders use the protected and quiet parallel routes; the model's mild stress penalty (×1.1 / ×1.25) sends them along the arterials next to them.
     - **Leisure trips.** The waterfront (Queens Wharf 1,170 counted against 32 modelled) carries leisure and other non-commute riding the model doesn't include.
   - **Next step:** fit the route-choice stress penalties (and the climb weight) to maximise agreement with the counters. This is a direct calibration of how much Wellington riders avoid traffic, and it replaces the guessed factors. Compare directional counts per countline, since many countlines count one side or one direction only.
4. **Directional use and before/after.**
   - Directional use: most cycle countlines record direction. Compare uphill and downhill counts on split streets (protected one way, sharrow the other) to see whether riders stay in the protected lane both ways, which may be illegal or unsafe, or ride the descent in traffic.
   - Before/after: several countlines started when routes opened (The Parade 2025, Thorndon Quay 2025/26, Molesworth Street Feb 2026). Their counts after opening, set against modelled potential, give a local estimate of how much new infrastructure lifts cycling. Use this in Phase 3.2.
   - E-scooters: counts show how much of the demand for bike lanes comes from micromobility.
5. **Check traffic counts.** Use the car, bus and van classes on the same sensors to check council ADT on about 130 streets.
6. **2018 vs 2023.** The census travel CSV already has `2018_*` columns on 2023 SA2 boundaries. Map where cycling grew. Early before/after evidence should come from routes such as Newtown, Kilbirnie and Thorndon.

## Phase 3: demand model (3–7 days)

1. **Education trips.**
   - Primary and secondary students use the PCT school models (Goodman et al. 2019), with centred gradient g − 0.63.
     - Primary: base −4.813 + 0.9743d − 0.2401d² − 0.4245g_c, plus 3.642 for Go Dutch, with no change beyond 5 km.
     - Secondary: base −7.178 − 1.870d + 5.961√d − 0.5290g_c, plus 3.574 + 0.3438d for Go Dutch, with no change beyond 10 km.
   - Tertiary students use the commute model.
   - School type and roll come from the MoE schools directory, whose catalogue API didn't respond (retry), or from OSM `isced:level` tags.
2. **A Wellington uptake model.**
   - Method: a grouped binomial model on SA2 pairs, 2018 and 2023, weighted by trips, with standard errors clustered by origin.
   - Terms:
     - the PCT distance and gradient terms;
     - share of the route at low stress, and the route's worst stress level;
     - share of the route on protected cycleways;
     - origin car-free households, student share and age;
     - a year dummy;
     - a tertiary-destination dummy.
   - Scenarios:
     - "Wellington with a full low-stress network": every route at level 1–2.
     - "Local Go Dutch": the Dutch intercept and distance shift applied to the local model.
   - This calibrates the hill penalty to how Wellingtonians actually ride. The 2018→2023 change on routes that gained cycleways gives a less confounded estimate of what infrastructure does.
3. **Trips other than commuting and education**, following Scotland's NPT method:
   - **Purposes:** shopping (OSM shops and supermarkets), leisure (parks, sport, entertainment) and visiting (SA1 population).
   - **Trip rates and purpose shares:** the Ministry of Transport NZ Household Travel Survey regional tables, downloaded by hand. The site blocks scripts. In Auckland, commuting plus education is about 16–22% of trips.
   - **Distribution:** a gravity model, fitted to relative trip lengths.
   - **Uptake:** the 2020 model, with shopping halved and floored at observed.
   - **Scaling:** scale to all-day flows with the counter factor k.
4. **Route choice.** Compute uptake separately on fastest, balanced and quietest routes (stress factors about 1.0, 1.5 and 3.0), each using that route's own length and gradient, as NPT does.

## Phase 4: prioritisation (3–5 days)

1. **Keep the trip table.** `cycle_model.py` should write out the trip table: origin node, destination node, trips by scenario and full-network distance.
2. **Measure connectivity.** Build the low-stress network: level 1–2 and protected edges, with up to about 150 m of level 3 allowed and no level 4 (Lowry et al. 2016).
   - A trip counts as connected if the low-stress route is at most 1.25 × the shortest route (Furth, Mekuria & Nixon 2016).
   - Report the percentage of trips connected for census, Go Dutch and e-bike.
3. **Candidates** are all level 3–4 corridors and arterial crossings, not only those above 250 trips/day. Each gets a treatment:
   - **Quiet street:** local or collector streets with ≤3,000 ADT after filtering get 30 km/h plus a modal filter. This is the cheapest option.
   - **Parallel route:** where a parallel low-stress route exists within 1.25× the distance, use it and fix its crossings.
   - **Protected lane:** arterials, bus routes and state highways with over 3,000 ADT or 50 km/h get a protected lane. On steep streets, protect uphill only and set 30 km/h downhill.
   - **Crossing:** signals or a raised crossing.
4. **Marginal benefit.**
   - First screen: count the trips joining the low-stress "islands" each candidate would connect.
   - Then, for the top ~100, rerun the routing with the candidate added.
   - Report ΔT (trips per day newly connected) and ΔK (their cycle-km).
5. **Greedy build order.** Repeatedly add the candidate with the highest ΔT per $M, recompute, and continue. Also test adjacent pairs, because links complement each other.
6. **Money, marked indicative (not an NZTA benefit–cost ratio).**
   - Costs: about $0.75M/km for transitional builds (Wellington 2023), permanent builds about 4.5× that, and a quoted national average of $1.6M/km.
   - Health benefit: NZTA MBCM (2023), $4.90 per new cyclist-km (e-bike $2.50), capped at $6,200 per new cyclist per year.
   - I couldn't verify MBCM's decongestion and crash values (NZTA blocks scripted access).
7. **Safety.**
   - Source: NZTA's Crash Analysis System, `CAS_Data_Public/FeatureServer/0`. Filter to `tlaName='Wellington City' AND bicycle>0`. This gives about 40–60 cyclist crashes a year, 8–12 of them serious or fatal.
   - Method: snap crashes to edges within 30 m and smooth crash rates per census cycle-km with empirical Bayes by road type.
   - Use: show crash risk as a flag and a tie-breaker, not a multiplier, because low current cycling suppresses crash counts.
8. **Uncertainty.**
   - Monte Carlo draws vary:
     - the detour cap (1.15 / 1.25 / 1.5);
     - how much level 3 is tolerated (0 / 150 / 400 m);
     - the scenario;
     - costs;
     - benefit per km (±30%).
   - Report median rank with a 10–90% band, and a "robust top 10": candidates that are top 10 in at least 80% of draws.

Output: `cycle_priorities.csv` with rank, corridor or crossing, treatment, length, cost range, ΔT, change in % trips connected, ΔT per $M, $ benefit per year, crash risk, plan status and build-order step.

## Phase 5: map and write-up (1 day)

- **New map layers:**
  - facility levels, like CyclOSM;
  - signed routes;
  - low-stress "islands" before and after the top 10;
  - crash points;
  - counters, comparing modelled and counted flows.
- **Scenarios:** add Go Dutch 2020, floored, and "Wellington full network".
- **Priority list:** replace the gap list with the prioritised list: treatment, cost, trips connected and robust rank band.
- **README:** update the methods and limitations.

## Things we couldn't verify (reviews could not reach the source)

- NZTA Cycling Network Guidance separation chart values. The site blocks scripted access, so Auckland Transport's design code was used as the NZ reference.
- NZTA MBCM decongestion, facility-quality and crash values. Some figures found online are Queensland's, not NZ's, and are not used here.
- The CROW table, read from a secondary reproduction (ECF SCAP guide).
- NZ Household Travel Survey purpose shares for Wellington, and any NZ e-bike uptake coefficients.
- The VivaCity data licence, which says only "open access" with no named licence. Attribute WCC.

## Main sources

- PCT: Lovelace et al. 2017, JTLU; github.com/ITSLeeds/pct (R/uptake.R); github.com/npct/pct-scripts; Goodman et al. 2019, J. Transport & Health (schools)
- NPT Scotland: github.com/nptscot/npt (R/uptake.R, R/utility_trips.R); github.com/nptscot/osmactive; Corenet, J. Transport Geography 2026
- LTS: Furth LTS tables v2 (2017); Conveyal R5 LevelOfTrafficStressLabeler; github.com/bikeottawa/stressmodel; PeopleForBikes brokenspoke-analyzer; github.com/SupaplexOSM/OSM-Cycling-Quality-Index; github.com/cyclosm/cyclosm-cartocss-style
- Guidance: UK LTN 1/20 (Fig 4.1); CROW via ECF SCAP guide; Auckland Transport TDM cycling design code; NZTA Cycling Network Guidance (neighbourhood greenways, route components)
- Connectivity: Furth, Mekuria & Nixon 2016 (TRR 2587); Lowry, Furth & Hadden-Loh 2016 (TR-A 86); Mahfouz, Lovelace & Arcaute 2023; CyIPT (cyipt.bike); TfL Strategic Cycling Analysis 2025
- NZ data: NZTA open data (NSLR, CAS, state highway monitoring sites, TMS); WCC Transport_Sensors and its public S3 bucket; Stats NZ 2023 Census travel OD; Metlink GTFS
