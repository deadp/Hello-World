# Harbour Quays bus priority: what it means for cycling

Scenario analysis, Go Dutch trips (a mode-shift target) and census (today's riders), weekday outbound trips, connectivity cap 1.25. Method: `scripts/cycle_harbour_quays.py`.

## Results

| Scenario | Level | Go Dutch newly connected | Go Dutch % connected (today > after) | Census newly connected | Census % connected (today > after) | Lane cost, central (range) $M |
|---|---|---|---|---|---|---|
| A: bus lanes as announced | all ages (stress 1) | +0 | 19.8% > 19.8% | +0.0 | 16.9% > 16.9% | 0 (in the $11M) |
| A: bus lanes as announced | confident riders (stress <= 2) | +0 | 35.7% > 35.7% | +0.0 | 35.5% > 35.5% | 0 (in the $11M) |
| B: A + protected lanes and junction priority | all ages (stress 1) | +857 | 19.8% > 21.2% | +62.2 | 16.9% > 17.7% | 3.3 (1.5-7.0) |
| B: A + protected lanes and junction priority | confident riders (stress <= 2) | +1,183 | 35.7% > 37.6% | +126.7 | 35.5% > 37.0% | 3.3 (1.5-7.0) |
| C: B + connectors (Bunny St, Featherston St, Taranaki St) | all ages (stress 1) | +2,004 | 19.8% > 23.1% | +270.1 | 16.9% > 20.2% | 4.6 (2.2-9.8) |
| C: B + connectors (Bunny St, Featherston St, Taranaki St) | confident riders (stress <= 2) | +1,304 | 35.7% > 37.9% | +137.0 | 35.5% > 37.2% | 4.6 (2.2-9.8) |

## Scenario A: bus lanes as announced

Bike-usable peak bus lanes do not connect any new trips: +0 Go Dutch trips at all ages and +0 for confident riders. Level 3 never counts as connected at either standard, and 3 is the best a bus lane earns here.
Of 92 on-road directions on the four bus-lane streets, 0 (0 m) get worse: every one is already level 3 (40) or level 4 (52) today, so the max(today, 3) rule changes nothing. No direction rated 1 or 2 is on an on-road lane there. A further 660 m of direction on these streets is a sidepath or protected lane and is left alone. Nothing gets better in the model either: off peak the lane is general traffic, so a level 4 direction is still level 4 outside the peaks.
Go Dutch trips lost: 0 (confident), 0 (all ages).
During the peak, a bike in a kerbside bus lane on a 50 km/h road is probably less stressful than sharing with two lanes of general traffic, but the model has no peak/off-peak split and buses at 30+ an hour are already rated 4. Read A as neutral for the model's standards, with a possible modest peak-hour gain for confident riders that this method cannot show.
The waterfront shared path beside Customhouse and Jervois Quay is not changed: 83 edges, 2,737 m, stress counts by direction {1: 158, 2: 8}. It stays the all-ages route along the harbour.

## Scenarios B and C: adding protected lanes

B (protected lanes on 2.07 street-km equivalent, signal priority at 2 internal junctions) connects +857 Go Dutch trips at all ages (+1.44 points) and +1,183 for confident riders (+1.99 points). Census trips: +62.2 and +126.7.
C adds 0.83 street-km equivalent of connectors and 0 more junctions. Go Dutch: +2,004 at all ages (+3.37 points), +1,304 for confident riders. The extra over B is +1,147 and +121.
Lane cost, central $1.6M a km: B $3.3M ($1.5-7.0M), C $4.6M ($2.2-9.8M). Junction priority is extra: B 2 junctions, $1.6M central ($1.0-3.0M); C 2 junctions, $1.6M ($1.0-3.0M), an upper bound as some already have signals. Go Dutch trips connected per $M of lanes at all ages: B 259, C 433.

## Where the gain goes (origin suburbs, Go Dutch trips newly connected)

- B: A + protected lanes and junction priority, all ages (stress 1): Te Aro +381; Mt Victoria +128; Mt Cook +103; Thorndon +59; Newtown +48; Wellington Central +35.
- B: A + protected lanes and junction priority, confident riders (stress <= 2): Te Aro +465; Mt Victoria +135; Mt Cook +119; Thorndon +79; Newtown +72; Wellington Central +60.
- C: B + connectors (Bunny St, Featherston St, Taranaki St), all ages (stress 1): Te Aro +664; Mt Victoria +272; Newtown +230; Mt Cook +218; Thorndon +114; Island Bay +81.
- C: B + connectors (Bunny St, Featherston St, Taranaki St), confident riders (stress <= 2): Te Aro +469; Thorndon +155; Mt Victoria +138; Mt Cook +122; Newtown +75; Wellington Central +61.

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
- Runtime 1,236 s. Sources: metlink.org.nz project timeline for Harbour Quays; transportprojects.org.nz Harbour Quays bus priority (researched 30 Sep 2026).
