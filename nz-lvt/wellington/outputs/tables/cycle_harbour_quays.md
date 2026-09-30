# Harbour Quays bus priority: what it means for cycling

Scenario analysis, Go Dutch trips (a mode-shift target) and census (today's riders), weekday outbound trips, connectivity cap 1.25. Method: `scripts/cycle_harbour_quays.py`.

## Results

| Scenario | Level | Go Dutch newly connected | Go Dutch % connected (today > after) | Census newly connected | Census % connected (today > after) | Lane cost, central (range) $M |
|---|---|---|---|---|---|---|
| A: bus lanes as announced | all ages (stress 1) | +0 | 2.9% > 2.9% | +0.0 | 2.8% > 2.8% | 0 (in the $11M) |
| A: bus lanes as announced | confident riders (stress <= 2) | +0 | 24.5% > 24.5% | +0.0 | 25.5% > 25.5% | 0 (in the $11M) |
| B: A + protected lanes and junction priority | all ages (stress 1) | +161 | 2.9% > 3.2% | +16.2 | 2.8% > 3.0% | 3.3 (1.6-7.1) |
| B: A + protected lanes and junction priority | confident riders (stress <= 2) | +501 | 24.5% > 25.4% | +77.6 | 25.5% > 26.5% | 3.3 (1.6-7.1) |
| C: B + connectors (Bunny St, Featherston St, Taranaki St) | all ages (stress 1) | +268 | 2.9% > 3.4% | +27.7 | 2.8% > 3.2% | 4.7 (2.2-9.9) |
| C: B + connectors (Bunny St, Featherston St, Taranaki St) | confident riders (stress <= 2) | +771 | 24.5% > 25.9% | +116.5 | 25.5% > 27.0% | 4.7 (2.2-9.9) |

## Scenario A: bus lanes as announced

Bike-usable peak bus lanes do not connect any new trips: +0 Go Dutch trips at all ages and +0 for confident riders. Level 3 never counts as connected at either standard, and 3 is the best a bus lane earns here.
Of 92 on-road directions on the four bus-lane streets, 0 (0 m) get worse: every one is already level 3 (40) or level 4 (52) today, so the max(today, 3) rule changes nothing. No direction rated 1 or 2 is on an on-road lane there. A further 660 m of direction on these streets is a sidepath or protected lane and is left alone. Nothing gets better in the model either: off peak the lane is general traffic, so a level 4 direction is still level 4 outside the peaks.
Go Dutch trips lost: 0 (confident), 0 (all ages).
During the peak, a bike in a kerbside bus lane on a 50 km/h road is probably less stressful than sharing with two lanes of general traffic, but the model has no peak/off-peak split and buses at 30+ an hour are already rated 4. Read A as neutral for the model's standards, with a possible modest peak-hour gain for confident riders that this method cannot show.
The waterfront shared path beside Customhouse and Jervois Quay is not changed: 83 edges, 2,737 m, stress counts by direction {1: 152, 2: 14}. It stays the all-ages route along the harbour.

## Scenarios B and C: adding protected lanes

B (protected lanes on 2.08 street-km equivalent, signal priority at 2 internal junctions) connects +161 Go Dutch trips at all ages (+0.30 points) and +501 for confident riders (+0.92 points). Census trips: +16.2 and +77.6.
C adds 0.83 street-km equivalent of connectors and 0 more junctions. Go Dutch: +268 at all ages (+0.49 points), +771 for confident riders. The extra over B is +107 and +270.
Lane cost, central $1.6M a km: B $3.3M ($1.6-7.1M), C $4.7M ($2.2-9.9M). Junction priority is extra: B 2 junctions, $1.6M central ($1.0-3.0M); C 2 junctions, $1.6M ($1.0-3.0M), an upper bound as some already have signals. Go Dutch trips connected per $M of lanes at all ages: B 48, C 58.

## Where the gain goes (origin suburbs, Go Dutch trips newly connected)

- B: A + protected lanes and junction priority, all ages (stress 1): Te Aro +39; Kaiwharawhara +30; Mt Victoria +30; Wellington Central +25; Pipitea +7; Kelburn +7.
- B: A + protected lanes and junction priority, confident riders (stress <= 2): Te Aro +73; Mt Victoria +43; Wellington Central +37; Mt Cook +29; Seatoun +28; Newtown +27.
- C: B + connectors (Bunny St, Featherston St, Taranaki St), all ages (stress 1): Te Aro +86; Kaiwharawhara +43; Wellington Central +39; Mt Victoria +33; Pipitea +22; Thorndon +12.
- C: B + connectors (Bunny St, Featherston St, Taranaki St), confident riders (stress <= 2): Te Aro +169; Wellington Central +55; Mt Victoria +46; Pipitea +40; Thorndon +38; Mt Cook +30.

## Why these connectors

Bunny Street is the council's planned link from the Thorndon Quay cycleway to the waterfront, and it meets the corridor at the station end. The Featherston Street link runs alongside Whitmore Street to the same junction. Taranaki Street is the one route that leaves the corridor toward Courtenay Place and Te Aro. The southern end already meets the Newtown to City Cycleway and the Mt Victoria tunnel path at the Basin Reserve, so no connector was added there.

## Road reserve width (feasibility)

| Street | Role | Edges | Length m | Reserve median m | Reserve min m | Edges with a value |
|---|---|---|---|---|---|---|
| Bunny Street | connector: Bunny Street | 15 | 394 | 24.9 | 24.9 | 1 |
| Featherston Street | connector: Featherston Street | 10 | 510 | 29.4 | 25.1 | 6 |
| Taranaki Street | connector: Taranaki Street | 13 | 520 | 25.6 | 20.1 | 11 |
| Cable Street | corridor | 20 | 708 | 20.3 | 20.1 | 17 |
| Cambridge Terrace | corridor | 20 | 806 | 46.6 | 17.9 | 14 |
| Customhouse Quay | corridor | 17 | 858 | 31.0 | 18.3 | 15 |
| Jervois Quay | corridor | 30 | 1,436 | 30.7 | 28.5 | 21 |
| Kent Terrace | corridor | 13 | 802 | 46.6 | 16.5 | 12 |
| Wakefield Street | corridor | 29 | 923 | 20.2 | 20.1 | 21 |
| Whitmore Street | corridor | 10 | 309 | 25.3 | 25.1 | 4 |

Rough arithmetic, not a design: two 3.2 m general lanes each way, two 2.5 m protected lanes with buffers and two 3 m footpaths need about 24 m, before any bus lane. Streets with a 20 m reserve (Cable Street, Wakefield Street) are tight; Cambridge and Kent Terrace (about 46 m) have room. The bus lanes are announced to be kerbside, so protected lanes would compete with them for the same width, or replace a general lane.

## Caveats

- The bus lanes are peak-only. Off peak they are general traffic, so they do not make the street safe for everyone. They suit confident riders at peak, if the buses are frequent and slow.
- We cannot see the detailed designs. Scenario A is a stress rule (max of today and 3), not a design. Protected lanes are not announced; B and C show what they would add.
- Sidepaths (shared footpaths) are rated stress 1 in the network model and left as they are. Much of Cable Street, Cambridge and Kent Terrace, and parts of Jervois Quay already have one, so B adds less there than the length suggests. The model does not know whether they are wide or busy enough.
- The trip model has no peak/off-peak split and no bus-lane effect. Connected trips are a measure of network access, not a forecast of extra riders.
- Costs are indicative, from per-km rates, not project estimates. Junction costs are an upper bound.
- The Featherston Street and Taranaki Street connector limits are set by coordinates (north of the Whitmore/Customhouse junction; Wakefield Street to Courtenay Place). Check on a map.
- Runtime 146 s. Sources: metlink.org.nz project timeline for Harbour Quays; transportprojects.org.nz Harbour Quays bus priority (researched 30 Sep 2026).
