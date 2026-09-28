# Wellington City: what if rates were set on land value?

A parcel-level model of moving Wellington City Council's **general rate** from capital
value (CV) to land value (LV), using the council's own public valuation roll. It follows
the approach Lars Doucet and the Center for Land Economics use for US cities
([*Does Georgism Work? Five Years Later*](https://www.astralcodexten.com/p/does-georgism-work-five-years-later)),
adapted to New Zealand's rating system.

This matters now: in 2026 the council agreed to **consult on switching from capital value
to land value rating** (and on reducing its 3.7× commercial differential) for the
2027–37 Long-term Plan.

## Headline findings

All scenarios raise the same general-rate revenue as today (about **$396m** modelled on
82,313 rateable units). Targeted rates (water, sewerage, stormwater, downtown levy) are unchanged
and excluded.

| Scenario | Commercial share of general rate | Median house | Median apartment / unit | Vacant land & low-improvement sites |
|---|---|---|---|---|
| **Today**: CV, commercial pays 3.7× | 42.7% | $3,000 | $1,779 | $23.6m |
| LV, keep 3.7× | 36.4% | $3,315 (+11%) | $1,612 (−11%) | $46.5m |
| LV, residential/commercial revenue split held | 42.7% | $2,989 (0%) | $1,454 (−19%) | $52.3m |
| LV, differential cut to 2.0× | 23.6% | $3,981 | $1,936 | $34.7m |
| LV, no differential | 13.4% | $4,514 | $2,196 | $25.2m |
| CV, differential cut to 2.0× (for comparison) | 28.7% | $3,731 | $2,212 | $18.2m |

1. **Land is a big deal in Wellington.** Land is 54% of the rateable capital value
   ($48.6bn of $90.4bn), and 56% for residential property.
2. **Vacant land and land-banked sites roughly double their rates** under every LV
   scenario. The 706 commercial sites with little or no building (surface car parks,
   cleared lots, quake-prone buildings valued at nil) go from about $19m to $38m. This
   is the incentive Georgists want: holding well-located land empty gets more expensive.
3. **Apartments are the clearest winners.** They sit on small land shares, so their
   bills fall 11–19%. Te Aro and Pipitea homeowners see median falls of 27–30%.
4. **Older, land-rich suburbs pay more; newer northern suburbs pay less.** Houses in
   Newtown, Berhampore, Kelburn, Hataitai, Kilbirnie and Seatoun (land is over 60% of
   value) rise 24–31% under "LV, keep 3.7×". Churton Park, Tawa, Grenada North and
   Broadmeadows (land about 45%) fall 10–16%. The split follows each suburb's land share.
5. **Keeping the 3.7× commercial differential while moving to LV shifts about $25m
   a year from commercial ratepayers to residential ones.** Built commercial property
   pays $44m less and commercial land with little or no building pays $19m more.
   Commercial value is 43% land compared with 56% for residential, so taxing land cuts
   the commercial base. That is what pushes the median
   house up 11%. If the council holds each sector's revenue share constant instead, the
   median house is unchanged and 58% of homes pay less.
   **The rating base and the differential cannot be decided separately:** cutting the
   differential on top of an LV switch compounds the shift onto homeowners.
6. **This differs from the US results.** Doucet reports that the median single-family
   homeowner usually wins under a US land value tax shift. In Wellington the median
   house wins only if the commercial/residential split is held. Wellington already taxes
   commercial property heavily, and much of its housing is old, cheap-to-rebuild buildings
   on expensive land.
7. **The land values look fit for purpose.** Doucet's Baltimore report found vacant lots
   valued at about a tenth of the land value per m² of the improved lot next door.
   Wellington doesn't show that. Across 1,701 vacant lots compared with same-zone,
   similar-size improved neighbours within 300 m:
   - Vacant commercial land is valued at a median **1.01×** its neighbours' land $/m².
   - Vacant residential land is valued at 0.89× (many of those sections are steep or
     access-only).
   - Only 10% of vacant commercial lots fall below half their neighbours' rate.

## Figures

![Change by property type](outputs/figures/change_by_property_type.png)

![Change by suburb](outputs/figures/change_by_suburb.png)

| | |
|---|---|
| ![Land value per m²](outputs/figures/map_land_value_per_m2.png) | ![Change in rates bill](outputs/figures/map_rates_change.png) |

Tables behind every number are in [`outputs/tables/`](outputs/tables/).

## Land value and rates by block

`scripts/blocks.py` groups land into blocks: regular grids (100 m, 250 m,
500 m, 1 km), Stats NZ SA1s and SA2s, and SA2 × district plan zone "value districts".
It then answers two questions.

**1. How much land value, and how much land value rate, sits in each block?**
[`outputs/blocks/`](outputs/blocks/) has GeoJSON for the 250 m grid, the 500 m grid,
SA1s and SA2s, ready for QGIS or a web map. [`blocks_sa1.csv`](outputs/tables/blocks_sa1.csv)
and [`blocks_sa2.csv`](outputs/tables/blocks_sa2.csv) are the table versions. Each block has:
- land value per m² of private land, and per hectare of block (including roads);
- today's general-rate take;
- the take under land value rating (3.7× kept), both in total and per m² of land;
- the residential-only rates now and under land value rating;
- for SA1s only, 2023 Census residents, land value per resident, and residential rates
  per resident.

For example, Wellington Central land averages $6,022/m², which would carry about
$114/m² a year in general rates on land value. Mt Victoria North is $3,346/m² (about
$21/m² a year) and Grenada North $142/m² (about $1.40/m² a year).

![Land value per hectare, 250 m blocks](outputs/figures/map_blocks_250m_land_value_per_ha.png)

**SA1s** are Stats NZ's smallest standard output areas. Wellington City has 1,401 SA1s,
each with a median of 42 parcels and 141 residents (2023 Census). At that resolution:
- **Most areas pay more.** Residential rates rise in 73% of SA1s, home to 71% of
  residents, when the 3.7× commercial differential is kept. That is mostly the ~$25m
  shift from commercial to residential ratepayers described above; holding the sector
  split would lower the bar everywhere.
- **Per resident**, the median SA1's residential general rate goes from $1,048 to $1,174.
- **The pattern repeats street by street.** Apartment blocks in Te Aro and Pipitea, and the
  newer subdivisions of Churton Park, Grenada and Tawa, pay less.
  Almost all of the older southern and eastern suburbs pay more.

| | |
|---|---|
| ![Land value per m² by SA1](outputs/figures/map_sa1_land_value_per_m2.png) | ![Residential rates change by SA1](outputs/figures/map_sa1_residential_rates_change.png) |

**2. Could a council value land with one rate per block instead of parcel by parcel?**
This is the Qingdao and Somers approach Doucet describes: every parcel in a block gets
the same $/m², optionally adjusted for lot size.

The test works like this:
- Each urban parcel (≤ 2 ha, land value ≥ $50k) is valued with its block's rate,
  calculated *without* that parcel (leave-one-out), so no parcel sets its own value.
- The result is compared with its official 2024 land value using IAAO ratio-study
  statistics: COD measures how scattered the valuations are, and PRD measures whether
  they are fair between cheap and expensive land.
- About 2% of extreme ratios are trimmed.

| Blocks | Flat $/m²: COD | + lot-size adjustment: COD | Within ±20% | PRD |
|---|---|---|---|---|
| 100 m grid | 23.5 | **14.6** | 75% | 1.08 |
| SA1 | 25.7 | 15.9 | 72% | 1.12 |
| 250 m grid | 26.3 | 16.7 | 70% | 1.13 |
| 500 m grid | 27.9 | 18.5 | 66% | 1.18 |
| SA2 × zone | 29.2 | 19.3 | 64% | 1.17 |
| SA2 | 30.1 | 20.3 | 62% | 1.20 |
| 1 km grid | 30.6 | 20.9 | 61% | 1.22 |

IAAO targets are COD ≤ 15 for residential land (≤ 20 for vacant land) and PRD between
0.98 and 1.03.

- **Lot size matters more than block size.** Land $/m² falls roughly with the square
  root of lot area (slope about −0.5), so a large section is worth about 1.4× a section
  half its size, not 2×. Adding this one citywide adjustment cuts COD by about a third
  at every block size.
- **Small blocks with a size adjustment are nearly as good as parcel-by-parcel
  valuation:** 100 m blocks meet the IAAO residential COD standard. SA1s (COD 15.9) come
  close and beat a 250 m grid with a similar number of blocks, because their boundaries
  follow real neighbourhood edges (ridgelines, main roads, zone changes) rather than an
  arbitrary grid.
- **Coarse blocks are regressive.** PRD rises from 1.08 to 1.22 as blocks grow: modest
  land is over-valued and premium land (views, sun, frontage) under-valued within the
  block. A block-rate system would need small blocks, or a premium/discount layer, to
  avoid shifting rates onto cheaper sections.
- This compares block rates with QV's parcel mass appraisal, not with sale prices, so it
  measures how much parcel-level detail a block rate keeps. It isn't a test of market
  accuracy.

Full results, including how rates bills would differ, are in
[`blocks_valuation_accuracy.csv`](outputs/tables/blocks_valuation_accuracy.csv).

![Block valuation accuracy](outputs/figures/blocks_valuation_accuracy.png)

## Rates vs cost of service (Urban3-style)

[`scripts/fiscal.py`](scripts/fiscal.py) sets each property's estimated 2025/26 rates bill
against an estimate of what it costs the council to serve. It follows Urban3's "fiscal
productivity" method: local roads and pipes are charged to the properties they run past,
and shared costs are spread across everyone.

2025/26 is the last year water was billed in council rates (Tiaki Wai bills it from 1 July
2026), and the first year of the 2024 valuations. All figures exclude GST.

**Revenue: each rating unit's bill from the 2025/26 rates resolution.** It includes the
general rate, water, sewerage, stormwater, the sector rates and the downtown levy. The
modelled total is $611.8m, 97.5% of the actual $627.7m.

**Costs: two views.** Activity figures come from the LTP 2024-34 Amendment
([`fiscal_cost_pools_$m.csv`](outputs/tables/fiscal_cost_pools_$m.csv)).
- **A. Rates-funded:** what rates pay for today. Water $92m, wastewater $82m, stormwater
  $45m, transport $104m, and $261m for everything else (parks, libraries, recreation,
  governance, and so on).
- **B. Full cost:** operating cost + interest + depreciation, net of fees and subsidies.
  This is **$694m, about $82m a year (13%) more than rates raise.** The gap is mostly
  depreciation on the three waters that current rates don't cover: water rates cover
  about 78% of the full cost, wastewater 68% and stormwater 80%.

**How costs are allocated.** [`network_frontage.py`](scripts/network_frontage.py)
does the frontage matching. It uses the council's GIS: 709 km of council roads and
2,700 km of council pipes.
- **Local roads and pipes (up to 300 mm)** go to the properties on each side, in
  proportion to frontage. Pipe cost is weighted by diameter.
- **Trunk mains, treatment, arterial roads and other transport costs** are shared per
  rating unit. Trunk stormwater is shared by land area instead.
- **Everything else** is shared per rating unit.
- **The sector rates and downtown levy** are treated as funding that sector's own
  spending.
- **Frontage past parks and schools** is spread across all ratepayers.

| Property type | Rating units | Median rates | Local roads + pipes cost | Rates ÷ cost (A) | Rates ÷ cost (B) | A, shared costs by CV |
|---|---|---|---|---|---|---|
| House | 51,549 | $4,850 | $2,000 | 0.72 | 0.62 | 0.70 |
| Apartment / unit / flat | 25,814 | $3,070 | $460 | 0.53 | 0.48 | 0.91 |
| Commercial | 1,436 | $43,600 | $5,140 | **5.74** | 5.30 | 2.36 |
| Commercial land, little or no building | 706 | $20,400 | $4,510 | 2.83 | 2.46 | 1.94 |
| Residential vacant land | 2,322 | $2,460 | $2,400 | **0.32** | 0.28 | 0.42 |
| Rural / lifestyle | 486 | $5,970 | $5,290 | 0.68 | 0.61 | 0.53 |

"Local roads + pipes cost" is the average annual cost of the local network per unit.

1. **Commercial ratepayers carry the city.**
   - Commercial property pays $216m of rates against about $38m of allocated cost.
   - This is the 3.7× differential at work.
   - Even if shared costs are allocated by capital value, commercial still pays 2.4× its
     cost.
   - Residential property as a whole pays about two-thirds of what it costs to serve.
2. **Local infrastructure per home falls steeply with density.** Annual cost of local
   roads and pipes per dwelling:

   | Dwellings per hectare | Local network cost per dwelling (A) |
   |---|---|
   | under 5 | $6,000 |
   | 5–15 | $2,140 |
   | 15–30 | $1,910 |
   | 30–60 | $1,200 |
   | 60–150 | $595 |
   | 150+ | $117 |

   This is the classic Urban3 result: spread-out development needs far more road and
   pipe per household. Sparse suburban lots cost many times more to reach than
   apartments.
3. **Vacant residential land pays about a third of its cost.** It sits on serviced
   streets but pays rates on low capital value. Taxing land value instead of capital
   value (the rest of this analysis) would close most of that gap.
4. **Whether apartments "pay their way" depends on how shared services are split.**
   - Per rating unit (every household uses libraries and parks equally): apartments
     pay 0.53 of their cost, and inner-city SA2s such as Te Aro look subsidised.
   - By capital value: apartments pay 0.91, and 150+ dwellings/ha pays its way (1.08).
   - Either way, their *local infrastructure* cost is tiny.

![Rates vs cost by property type and density](outputs/figures/fiscal_revenue_to_cost.png)

![Rates and net per hectare](outputs/figures/fiscal_map_rates_and_net_per_ha.png)

The map shows rates per hectare (left, Urban3's "value per acre" in rates terms) and
rates minus cost of service per hectare (right). The CBD, Kilbirnie and Johnsonville
commercial centres and the airport pay well above their cost. Most residential land pays
less, because under the 3.7× differential commercial ratepayers fund much of the
residential share.

### Net by suburb and interactive map

![Net by suburb](outputs/figures/fiscal_net_by_suburb.png)

The central city and commercial areas pay far more than they cost: Wellington Central
+$75m, Pipitea +$21m, Te Aro +$18m, Thorndon +$10m and Rongotai +$9m a year. The big
residential suburbs cost more than they pay: Tawa −$18m, Karori −$12m and Johnsonville
−$11m ([`fiscal_net_by_suburb.csv`](outputs/tables/fiscal_net_by_suburb.csv)).

[`scripts/fiscal_blocks.py`](scripts/fiscal_blocks.py) rolls the results up to SA1s
([`outputs/blocks/sa1_fiscal.geojson`](outputs/blocks/sa1_fiscal.geojson)).
[`scripts/build_web_map.py`](scripts/build_web_map.py) builds an interactive page
([`outputs/web/who_pays_wellington.html`](outputs/web/who_pays_wellington.html)) with a
map by SA1. It switches between net per hectare (rates-funded or full cost), rates and
cost per hectare, local infrastructure per home, land value per m², and the change under
land value rating. Clicking a suburb in the chart zooms the map to it.

### Would land value rating bring rates closer to cost?

[`scripts/lv_alignment.py`](scripts/lv_alignment.py) re-levies the same $612m under
land-value designs and measures how far each property's bill sits from its cost to serve
([`lv_alignment_per_unit.csv`](outputs/tables/lv_alignment_per_unit.csv),
[`lv_alignment_by_value.csv`](outputs/tables/lv_alignment_by_value.csv)).

**What "cross-subsidy" means here:** the total of rates paid above cost, which funds
others. Lower means rates track cost more closely. Each cell gives two figures: shared
costs split per property / split by value.

| Design (all raise the same total) | Cross-subsidy | Bills within ±25% of cost | House rates ÷ cost | Commercial rates ÷ cost |
|---|---|---|---|---|
| Today (capital value, commercial 3.4×) | $215m / $142m | 24% / 54% | 0.72 | 5.7 / 2.4 |
| Land value, same residential/commercial split | $220m / $155m | 24% / 44% | 0.73 | 4.8 / 2.0 |
| Land value, today's commercial loading | $196m / $132m | 29% / 52% | 0.79 | 4.1 / 1.7 |
| Land value, commercial 2× | $159m / $102m | 37% / 60% | 0.91 | 3.0 / 1.2 |
| Land value, no commercial loading | $132m / $91m | 43% / 59% | 1.03 | 1.9 / 0.8 |
| Land value + maximum fixed charge ($1,752, the 30% legal cap) | $142m / $111m | 46% / 59% | 0.84 | 3.2 / 1.3 |

- **Switching the base from capital value to land value barely moves the overall
  picture.** On its own it changes the cross-subsidy by −9% to +10%. The biggest
  mismatch is between commercial and residential, set by the commercial differential,
  not by which value is taxed.
- **The commercial differential is the main lever.** Land value with no commercial
  loading cuts the cross-subsidy by about 40% under either cost view.
- **A uniform fixed charge gets the most individual bills near their cost** (46% within
  ±25%), because much of the council's cost is per property.
- **Land value clearly helps where Doucet predicts: vacant and underused land.**
  - Vacant residential land goes from paying 0.32 of its cost to 0.53–0.75.
  - Commercial sites with little or no building start paying well above their
    frontage cost.
- **It doesn't help Tawa or apartments.**
  - Tawa's land is among the city's cheapest per m², so with the split unchanged its net
    goes from −$18m to −$21m.
  - Apartments sit on little land, so their bills fall further below cost.
  - Only a smaller commercial loading narrows Tawa's gap.

![Land value rating and cost alignment](outputs/figures/lv_alignment.png)

**Caveats**
- The cost split is a model, not the council's cost accounting.
  - Local vs trunk shares use length weights (trunk 2.5×, arterials 2×). Pipe cost
    rises with diameter by assumption, not from asset valuations.
  - Water use isn't measured per property: the commercial metered-water revenue is
    spread by capital value.
- The rating category (residential vs commercial) is the same proxy as the rest of this
  analysis.
- Non-rateable land (parks, schools, Crown land) gets network frontage, but its costs are
  spread over ratepayers.
- Per-unit sharing treats a studio apartment and a large office building as one
  "household" each for shared services. That is why the capital-value alternative is
  shown.

### Cost model v2: asset-based, calibrated, demand-based sharing

This version uses only public data (`network_v2.py`, `fiscal_v2.py`). The revenue side and
the activity cost totals are the same as v1. Only the way costs are assigned to
properties changes. It was built in two steps: v2a (the first four rows below), then
v2 (calibration and neighbourhood pooling).

| | v1 | v2 |
|---|---|---|
| Pipe cost | Length × a diameter factor (1.0 / 1.3 / 1.7) | Replacement cost (diameter unit rate) ÷ life (by material) × condition grade (0.8–1.5) |
| Water mains | Frontage | 31,800 service connections traced to their main and property; each main is split among the properties connected to it |
| Arterial roads | Arterials (2×) all shared; fronting lots pay nothing | Fronting lots pay the local-street equivalent. Width and traffic (category weight × ADT) above that is citywide |
| Trunk, treatment, other transport | Equal per rating unit | Person-equivalents: residents + 0.35 × workers (water, wastewater); trips: residents + 0.8 × workers (transport) |
| Stormwater trunk | Land area | Impervious area: roof + 20% (residential) or 60% (commercial) of the rest of the lot |
| Parks, libraries, community, governance… | Equal per rating unit | 81.6% by people (residents + 0.1 × workers), 18.4% per rating unit |
| Local vs trunk | Trunk weighted 2.5× per km | Each pipe's own weight, **calibrated to the council's asset valuation** (below) |
| Sharing local cost | Each lot's own frontage | **Pooled by SA1, split by lot width** (√area) |

Demand comes from the 2023 census:
- **Residents:** SA1 usually resident population, split over the homes in each SA1.
  This gives 201k residents on rateable homes.
- **Workers:** 2024 Stats NZ employee counts by SA2, spread over the floor area of
  commercial and non-rateable property. Floor area is building footprint × storeys, with
  storeys = height ÷ 3.3 m. This gives 135k jobs on rateable property; jobs on
  non-rateable land are left out.

**Calibration to council costs.** Each network's total is already the council's
activity cost, so scaling all pipes up by the same factor would change nothing. What
matters is the split between local pipes and everything else.
- **Replacement cost.** The GIS pipe inventory, priced at generic unit rates, comes to
  less than half the council's replacement cost (Annual Report 2024/25, Table 36).
- **Assets that aren't pipes.** Part of the gap is reservoirs, pump stations and
  tunnels. I priced these from the counts in the Infrastructure Strategy:
  - 68 reservoirs at $3m / $6m / $10m each (low / central / high);
  - water pump stations at $1m / $1.5m / $2.5m;
  - 69 wastewater pump stations at $1.5m / $2.5m / $4m;
  - 13.7 km of tunnels not in the GIS at $10k / $15k / $25k per metre.

  These count as trunk assets.
- **Pipes.** The remainder scales the pipe unit rates by k = 2.0 (water), 2.2
  (wastewater) and 1.5 (stormwater). That is consistent with hillside construction and
  with the valuation including fittings and laterals.
- **Depreciation check.** Implied depreciation matches the council's 2025/26 figures
  reasonably well:
  - water $29m, against $35m for the whole activity;
  - wastewater $37m, against $50m including the treatment plants (~$350m of plant);
  - stormwater $29m, against $23m (the council assumes longer stormwater lives).

  So the published useful lives (40–130 years) are consistent with the weights.
- **Effect.** Local pipes' share of network cost falls to 70% (water), 66%
  (wastewater) and 42% (stormwater). About $6m a year of water cost moves from fronting
  lots into the demand-based trunk pool (`outputs/tables/fiscal_v2_calibration.csv`).

**Frontage vs lot size.** Frontage says how much network a neighbourhood needs (street
length per home). But which particular lot fronts which pipe is mostly noise: corner
lots pay double, rear lots on a right-of-way pay nothing, and one lot can pick up a main
that serves the whole street. v2 therefore:
1. adds up each SA1's local roads and pipes (frontage + traced connections + pipes
   crossing private land);
2. splits that total among its parcels by lot width (√area), since the street length a
   lot needs grows with its width, not its area;
3. splits each parcel's share among its units by CV.

Lot area alone would overcharge big, steep hillside lots and parks. The share falling on
parks and schools is shared citywide. Own frontage, lot area and equal per unit are run
as variants.

Mean local roads + pipes cost per home, rates-funded
(`outputs/tables/fiscal_v2_local_cost_by_rule.csv`):

| Homes at… | Own frontage | SA1 pool, lot width (v2) | SA1 pool, lot area | SA1 pool, per unit |
|---|---|---|---|---|
| <5 /ha | $3,831 | $4,426 | $6,462 | $2,586 |
| 15–30 /ha | $1,555 | $1,507 | $1,226 | $1,598 |
| 60–150 /ha | $541 | $429 | $326 | $1,092 |
| 150+ /ha | $129 | $93 | $78 | $467 |

**Results (rates-funded view A; ratio = rates ÷ cost; range across all v2 variants in brackets):**

| | v1 | v2a | v2 |
|---|---|---|---|
| Commercial | 5.74 | 2.62 | **2.61** (1.98–3.36) |
| Commercial land, little building | 2.83 | 2.17 | 2.17 (1.77–2.58) |
| House | 0.72 | 0.72 | 0.72 (0.69–0.76) |
| Apartment / unit / flat | 0.54 | 0.69 | 0.69 (0.62–0.74) |
| Residential vacant land | 0.32 | 0.76 | 0.74 (0.63–0.79) |
| Homes at <5 dwellings/ha | 0.64 | 0.79 | 0.76 (0.65–0.89) |
| Homes at 150+ dwellings/ha | 0.46 | 0.79 | 0.79 (0.71–0.85) |
| Tawa (all property; net) | 0.56 (−$18.3m) | 0.60 | 0.60 (−$15.6m) |

The variants are:
- sharing by own frontage, lot area, or equally per unit;
- low or high non-pipe asset values;
- trunk pipes costing 1.5× more;
- worker weights halved or doubled;
- a 60% (not 81.6%) people share of other services.

The sharing rule matters most for the sparsest homes (0.65 by lot area to 0.89 per
unit). Worker weights matter most for commercial property.

**What changes from v1:**
- **Commercial still pays more than it costs, but by 2.6× not 5.7×.** v1 charged an
  office tower as one household for shared services. v2 charges it for the people who
  work there.
- **Apartments move towards paying their way (0.54 → 0.69).** They average 2.0 residents
  against 2.9 per house, so they use less of the people-based services.
- **The downtown gap mostly disappears.** Te Aro homes go from −$20m to −$3m, and
  Wellington Central and Pipitea come close to break-even.
- **Vacant residential land improves (0.32 → 0.74)** because it has no residents. It
  still has pipes and roads out front.
- **Houses and Tawa hardly move under any version or variant.** Their gap was never
  about the sharing rule or the calibration. It is the cost of the local network per
  household at 15–30 dwellings/ha, plus a general rate on CV that is low relative to
  that cost. The worst-off suburbs are still the northern ones (Paparangi, Grenada
  North, Kingston, Newlands, Tawa: 0.55–0.57).
- **Calibration and pooling barely move the aggregates, but they move individual
  properties a lot.** That is expected: they change who within a neighbourhood pays,
  not how much the neighbourhood costs.

Onslow examples (`python scripts/explain_property.py "Onslow Road" --v2`):

| Property | Rates | v1 cost | v2 cost (variant range) | Local roads + pipes: own frontage → v2 (SA1 pool by width) |
|---|---|---|---|---|
| 3 Onslow Rd (461 m²) | $4,344 | $6,975 | $6,718 ($5.6–7.0k) | $1,076 → $1,923 |
| 9C Onslow Rd (2,279 m²) | $4,213 | $8,125 | $9,473 ($7.1–9.7k) | $1,875 → $4,276 |
| 17C Onslow Rd (3,691 m², right-of-way) | $4,256 | $14,010 | $11,021 ($7.5–11.9k) | $5,187 → $5,441 |

Pooling spreads the Onslow SA1's expensive right-of-way pipes over the neighbourhood.
Bigger lots then carry more because they are wider, not because they happen to sit on a
main. The per-property shared charge falls from $5,033 (v1) to about $2,800, plus
$1,200–2,000 in demand-based trunk and treatment.

**Still not fixable from public data:**
- actual water consumption (commercial meters);
- the council's per-asset valuation (the calibration uses class totals);
- road structure costs (retaining walls, bridges) by location;
- true rating categories;
- where people actually live within unit-title buildings.

![Cost model versions](outputs/figures/fiscal_v2_vs_v1.png)

## Where should new cycle connections go?

`fetch_cycling.py`, `cycle_network.py`, `cycle_model.py`, `cycle_gaps.py` and
`build_cycle_map.py` implement a Propensity to Cycle Tool (PCT) style analysis. The output is
the interactive map `outputs/web/wellington_cycle_gaps.html` and the static map
`outputs/figures/cycle_gaps.png`.

**Method**
1. **Trips.** 2023 Census main means of travel to work and to education, from SA2 of
   residence to SA2 of workplace or institution (Stats NZ). This gives 97k work and 47k
   education commuters with both ends in the study area; people who work or study at home
   are left out. Where trips start and end within an SA2:
   - homes are placed by SA1 population;
   - workplaces by estimated workers (SA2 job counts over floor area, from `fiscal_v2.py`);
   - education trips at OSM schools and universities.
2. **Network.** OSM streets and paths bikes may use (motorways excluded; unsigned
   paths and footways excluded unless paved or signed for bikes). Each edge gets:
   - its climb in each direction, from the WCC 1 m LiDAR DEM resampled to 5 m;
   - its existing facility, from OSM plus the council's Strategic Bike Network 2022;
   - its traffic, from council ADT counts and speed limits;
   - a simplified level of traffic stress.

   A road counts as protected if a cycleway or shared path runs alongside it.
3. **Routing.** Directed shortest paths with cost = metres × stress factor + 10 × metres
   climbed. The stress factor is 1.1 on stress level 3 and 1.25 on level 4, a mild
   preference that uses parallel paths without long detours. Outbound and return trips are
   routed separately.
4. **Uptake.** The PCT Go Dutch commute model (pct R package) gives the chance a trip is
   cycled from its route length and average gradient. The e-bike scenario adds the PCT's
   e-bike terms. "Census" routes the people who actually cycled in 2023.
5. **Gaps.** Street edges with 250+ Go Dutch trips a weekday and stress level 3–4 (over
   ~2,000 vehicles/day or over 50 km/h) with no protection. Touching gap edges on the same
   street (within 150 m) form a corridor. Corridors are ranked by potential cycle-km a day.

**Results**
- **Uptake.** 17% of work and study trips could be cycled in the Go Dutch scenario,
  against 2.7% who cycled in 2023 (3.4% of work trips). With e-bikes it is 29%. The hills
  cost Wellington roughly a third of the Dutch-flat potential. E-bikes more than make it
  up.
- **Where the potential cycling would ride.** 46% on protected routes (including
  parallel paths such as the Hutt Road and Tawa paths), 22% on quiet streets and 32% on
  busy unprotected streets.
- **Top 20 gap corridors vs the council's plan.** 17 are already in the 2022 Strategic
  Bike Network, but 9 of those were staged under Let's Get Wellington Moving, which was
  wound up in 2024. The CBD links (Willis, Taranaki, The Terrace, Courtenay Place,
  Victoria Street, Lambton Quay) are mostly ex-LGWM. Three are not in the plan at all:
  - SH2 Hutt Road between Ngauranga and Petone, which NZTA's Te Ara Tupua path addresses.
    OSM (May 2026) maps only part of that path.
  - Boulcott Street (Kelburn/Terrace to the CBD).
  - Takapu Road (Tawa/Grenada North).

  Tauhinu Road (Miramar) is 23rd.

| # | Street | Suburbs | Length | Today | Go Dutch | E-bike | Plan |
|---|---|---|---|---|---|---|---|
| 1 | Hutt Road (SH) | Ngauranga, Horokiwi, Newlands | 7.5 km | 55 | 667 | 1,465 | Not in plan |
| 2 | Middleton Road | Glenside, Churton Park, Johnsonville | 4.2 km | 14 | 687 | 1,984 | Planned (WCC) |
| 3 | Willis Street | Te Aro, Wellington Central, Aro Valley | 1.2 km | 367 | 1,485 | 2,624 | Planned (ex-LGWM) |
| 4 | Taranaki Street | Te Aro, Mt Cook, Wellington Central | 1.4 km | 235 | 1,120 | 1,867 | Planned (ex-LGWM) |
| 5 | The Terrace | Wellington Central, Te Aro | 1.6 km | 461 | 967 | 2,120 | Planned (ex-LGWM) |
| 6 | Courtenay Place | Te Aro | 0.9 km | 338 | 1,043 | 1,725 | Planned (ex-LGWM) |
| 7 | Broadway | Miramar, Strathmore Park | 1.5 km | 107 | 592 | 1,003 | Planned (WCC) |
| 8 | Tory Street | Te Aro | 0.8 km | 336 | 1,124 | 1,786 | Planned (WCC) |
| 9 | Burma Road | Broadmeadows, Khandallah, Johnsonville | 1.5 km | 44 | 581 | 1,369 | Planned (WCC) |
| 10 | Boulcott Street | Wellington Central, Mt Victoria, Kelburn | 0.6 km | 510 | 1,230 | 2,370 | Not in plan |
| 11 | Upland Road | Kelburn, Northland | 1.0 km | 149 | 754 | 1,627 | Planned (WCC) |
| 12 | Victoria Street | Wellington Central, Te Aro | 0.7 km | 266 | 1,032 | 1,724 | Planned (ex-LGWM) |
| 13 | Riddiford Street | Newtown | 0.6 km | 392 | 1,170 | 2,014 | Planned (WCC) |
| 14 | Moxham Avenue | Hataitai | 0.6 km | 370 | 1,191 | 2,211 | Planned (ex-LGWM) |
| 15 | Lambton Quay | Wellington Central, Pipitea | 0.9 km | 154 | 724 | 1,196 | Planned (ex-LGWM) |
| 16 | Takapu Road | Tawa, Grenada North, Takapu Valley | 1.2 km | 0 | 473 | 1,083 | Not in plan |
| 17 | Wellington Road (SH) | Hataitai, Kilbirnie | 0.7 km | 215 | 881 | 1,592 | Planned (ex-LGWM) |
| 18 | Ruahine Street (SH) | Hataitai | 0.4 km | 428 | 1,376 | 2,530 | Planned (ex-LGWM) |
| 19 | Raroa Road | Kelburn, Aro Valley | 1.5 km | 37 | 356 | 909 | Planned (WCC) |
| 20 | Newlands Road | Newlands | 1.4 km | 42 | 357 | 846 | Planned (WCC) |

Trips are per weekday, both directions, averaged along the corridor.

**Limitations**
- **Only commuting and study trips are included** (about a third of all trips). Shopping,
  leisure, and parents' school runs are missing, and so is the 2018–2023 shift to working
  from home.
- **The uptake model was fitted to English commuting with Dutch benchmarks**, not to
  Wellington.
- **Some inputs are coarse or dated.** OSM tagging and the council's 2022 plan layer may
  lag recent builds or changes (e.g. transitional bike lanes). Traffic stress uses
  council ADT where matched and defaults for residential streets.
- **Routing ignores some real-world detail:** turn difficulty, intersections, and wind.
  The ranking is a long list for engineering assessment, not a design.

## Data

| Source | What | Notes |
|---|---|---|
| WCC `PropertyAndBoundaries/Property` (public ArcGIS layer) | 88,070 rating-unit records with capital, land and improvements value | Valuation date 1 Sept 2024, used for rates from 1 July 2025 |
| WCC 2024 District Plan zones | Operative zones | Used for the commercial and non-rateable proxies |
| Stats NZ SA2 2025 (via WCC GIS) | 86 Wellington City statistical areas | Used for block aggregation |
| Stats NZ SA1 2025 + 2023 Census usually resident population (Stats NZ ArcGIS) | 1,401 SA1s in Wellington City | SA1 blocks and per-resident figures |
| WCC 2025/26 Rating Policy | General rate on CV, no UAGC, 3.7× commercial and 5× downtown vacant/derelict differentials | Base rate 0.301493 c/$ incl. GST |

## Method

1. **Deduplicate.** Drop 4,049 zero-value parent records (their value sits on unit-title
   and cross-lease children). Drop 720 valued parents whose portions (e.g. "COMMERCIAL
   PORTION" / "RESIDENTIAL PORTION") sum to the parent; keeping both would double count
   $6.5bn.
2. **Remove probably non-rateable land.** Drop the open space, town belt, hospital,
   tertiary and corrections zones, plus schools, churches, Parliament grounds and marae
   (identified from legal descriptions).
3. **Assign a proxy rating category.** The public layer has no differential category, so:
   - A unit is **Commercial** if it sits in a centre, mixed-use, industrial, port,
     airport or waterfront zone, unless it looks like a dwelling (a unit title ≤ $1.5m CV,
     or a non-unit property < $2m CV with a building).
   - Portion labels override the zone rule.
   - This gives commercial 16.7% of CV, against the ~15% Business Central quotes for
     2025/26. Implied commercial share of the general rate is 42.7%, consistent with the
     48% of *total* rates (including commercial-only targeted rates) reported for 2025/26.
4. **Model the scenarios.** Each scenario sets the rate in the dollar so total general-rate
   revenue is unchanged, then compares each unit's bill with today's.
5. **Check horizontal uniformity.** Compare each vacant lot's land value per m² with the
   median of improved neighbours in the same zone, within 300 m, and at 0.5–2× its area.

### Sensitivity

The headline results barely move when the residential/commercial thresholds change:

| Unit / house CV thresholds | Commercial share of CV | Median house (LV, 3.7×) | Median house (split held) | Median apartment (LV, 3.7×) |
|---|---|---|---|---|
| $1.0m / $1.5m | 17.8% | +10.7% | −0.4% | −10.4% |
| $1.5m / $2.0m (used) | 16.7% | +10.6% | −0.2% | −10.7% |
| $2.5m / $3.0m | 15.8% | +11.0% | −0.3% | −10.5% |

### Limitations

- **The rating category is a proxy.** The council's rating information database has the
  real category for every unit; requesting it would remove the biggest source of error.
- **Rateability is a proxy.** The model covers $90.4bn of CV against QV's published
  rateable total of $98.8bn. The gap is probably utility networks (which have no parcel)
  plus units the proxy wrongly excludes.
- **The 5× downtown vacant/derelict differential isn't modelled.** It is small and needs
  a council list of affected sites. Those sites are rated at 3.7× here.
- **Only the general rate is modelled.** Targeted rates would stay on capital value unless
  the council also changes them.
- **This is a static model with no behavioural response.** It shows who pays what on
  today's valuations, not the development response a land value rate is meant to cause.

## Reproduce

```bash
pip install -r ../requirements.txt
python scripts/fetch_data.py      # ~100 MB from gis.wcc.govt.nz into data/raw/
python scripts/build_dataset.py   # data/processed/units.parquet
python scripts/model.py           # outputs/tables/*.csv, data/processed/results.parquet
python scripts/uniformity.py      # outputs/tables/uniformity_*.csv
python scripts/figures.py         # outputs/figures/*.png
python scripts/blocks.py          # outputs/blocks/*.geojson, outputs/tables/blocks_*.csv
python scripts/fetch_networks.py  # roads, three-waters pipes, downtown levy area
python scripts/network_frontage.py
python scripts/fiscal.py          # outputs/tables/fiscal_*.csv, outputs/figures/fiscal_*.png
python scripts/fiscal_blocks.py   # SA1 GeoJSON, net by suburb table + chart (v2 costs, v1 + variant ranges kept)
python scripts/build_web_map.py   # outputs/web/who_pays_wellington.html
python scripts/lv_alignment.py    # land value rating vs cost-of-service alignment
python scripts/explain_property.py "Onslow Road" --type House   # line-by-line rates and cost for matching addresses
python scripts/network_v2.py      # v2: asset-weighted pipes, traced water connections, road reserve test
python scripts/fiscal_v2.py       # v2: calibration, SA1 pooling, demand-based sharing + variants
python scripts/explain_property.py "Onslow Road" --type House --v2
python scripts/fetch_cycling.py   # OSM streets/paths, WCC LiDAR DEM, bike plan, census travel OD
python scripts/cycle_network.py   # routable network with hills and traffic stress
python scripts/cycle_model.py     # PCT Go Dutch / e-bike potential, routed flows
python scripts/cycle_gaps.py      # gap corridors vs council plan; map data and figure
python scripts/build_cycle_map.py # outputs/web/wellington_cycle_gaps.html   # incl. local cost under each sharing rule
```

## Possible next steps

- Get the council's rating information database with differential categories and
  rateability flags, and rerun.
- Model a phased transition (e.g. 25% LV per year) and hardship options for land-rich,
  low-income owners (e.g. rates postponement).
- Test **split-rate** options (e.g. 70% LV / 30% CV) alongside the all-or-nothing
  scenarios.
- Repeat for Hutt City, Porirua and Auckland. Auckland's 2012 amalgamation moved
  legacy council areas onto a single capital value basis, which may give a natural
  experiment for development effects (check which legacy areas were on LV).
