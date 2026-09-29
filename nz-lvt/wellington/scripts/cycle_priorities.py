"""Prioritise cycle network improvements for Wellington City: which links and crossings, in what
order, connect the most potential trips per dollar.

1. Trip table. cycle_model.run(collect_matrix=True) gives outbound trips by scenario (census,
   Go Dutch, e-bike, Wellington habits) from each home node to each destination node (work,
   education, shopping, leisure, visiting). Cached in data/processed/cycle_od_matrix.npz.
2. Connectivity (Mekuria, Furth & Nixon 2012; Furth, Mekuria & Nixon 2016; Lowry et al. 2016).
   Central standard "all ages": stress 1 links (protected lanes, tracks, paths, genuinely quiet
   streets) ending at crossings of stress <= 2 (signals, zebras, quiet junctions). A trip is
   connected when its least-effort route on that network takes at most `cap` x the effort of its
   least-effort route on the whole network (cap 1.25 central; 1.15 and 1.5 as sensitivities).
   Effort = metres + 30 x metres climbed (the calibrated route-choice weight), so a quiet detour
   over a hill does not count as a connection. Trips under 200 m are left out. The first and
   last arc of a trip (up to 150 m) may be any stress: homes and workplaces on arterials are
   reached along the kerb or footpath. Crossing stress is per junction, not per turn
   (conservative). Sensitivities: "confident" (stress <= 2: painted lanes and moderately busy
   streets count) and "confident_flat" (stress <= 2, distance only: the first version).
3. Candidates.
   Corridors: runs of the same street where a road direction is stress 2-4 and carries >= 30 Go Dutch
   trips a weekday (cycle_gaps.corridors with a lower threshold), top 200 by potential cycle-km.
   Each is extended to the same street's other level 3-4 edges within 150 m, so short low-flow
   pieces between gap runs are treated too (otherwise the treated route stays broken).
   Council plan: every unbuilt Strategic Bike Network link (planned, ex-LGWM, desired) with a
   level 3-4 direction and not already covered above, whatever its trips. One-way streets on the
   plan become two-way for bikes (a contraflow lane): e.g. Bunny Street, which would carry the
   Thorndon Quay cycleway through to the waterfront in both directions.
   Treatment: residential/unclassified/tertiary streets with <= 3,000 vehicles/day get a quiet
   street (30 km/h and a modal filter); other roads a protected lane. Either way the treated
   directions become stress 1, and the corridor's internal junctions stress 2. Crossings: the 80 busiest junctions where
   low-stress approaches meet an unsignalised level 3-4 road (cycle_gaps.crossings). Treatment:
   signals where the road carries > 8,000 vehicles/day or is faster than 50 km/h, otherwise a
   raised zebra or refuge.
4. Marginal benefit, exact. Adding a candidate's arcs A to the low-stress graph, the new
   low-stress distance is  d'(s,t) = min(d(s,t), min over tails a of A: d(s,a) + d_aug(a,t)),
   since any new route first joins a new arc at its tail. dT = trips newly connected per weekday
   (outbound; returns roughly double it), dK = their cycle-km.
5. Ranking and build order. Score = dT per $M (Go Dutch, cap 1.25) x a network bonus: 1.5 when
   both ends of a corridor join the existing low-stress cycle network (protected facilities,
   cycleways, living streets and pedestrian areas within 30 m), 1.25 for one end (crossings: next to it). The bonus is a judgment, standing for
   what the trip model misses (wayfinding, a legible network, riders who would go further on
   it); the draws vary it from 0 to 1. Build order: greedy on the score, re-evaluating after
   each step (links complement each other: a crossing can unlock a quiet street). Also
   reported: rank by trips connected, regardless of cost.
6. Money, indicative only (not an NZTA benefit-cost ratio). Costs per km: quiet street
   $0.1-0.3M (central 0.2), at least $0.03-0.1M a project; protected lane $0.75M (Wellington transitional, 2023) to $3.4M
   (permanent, ~4.5x), central $1.6M (quoted national average); x (1 - 0.4 x one-way share) when
   only one direction needs it, at least $0.1-0.5M (0.25) a project. Crossings: signals $0.5-1.5M (0.8), raised zebra/refuge
   $0.15-0.4M (0.25). Health benefit: NZTA Monetised Benefits and Costs Manual (2023) $4.90 per
   new cyclist-km, on the extra cycling (scenario minus census) of the newly connected trips,
   x 2 for returns x 250 weekdays; low = Wellington habits, high = Go Dutch.
7. Safety flag: CAS crashes involving a bicycle since 2016 within 30 m of the corridor or
   crossing (not a weight: low cycling suppresses crash counts where riding feels unsafe).
8. Uncertainty: 1,000 Monte Carlo draws of cap (1.15/1.25/1.5), scenario (Go Dutch/e-bike/
   Wellington habits/census), cost (uniform in each range) and network bonus (0-1). Each draw
   ranks candidates by score (single-step); report median rank, 10-90% band and the share of draws in the top 10
   ("robust" if >= 80%). The connectivity standard is reported separately (rank_confident,
   rank_confident_flat): it redefines the problem rather than adding noise.

Writes outputs/tables/cycle_priorities.csv, cycle_build_order.csv, cycle_connectivity.csv and
outputs/blocks/cycle_priorities.geojson (WGS84, for the web map).
"""

import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

import cycle_gaps as CG
import cycle_model as M

PROC, TABLES, RAW = M.PROC, M.TABLES, M.RAW
BLOCKS = M.ROOT / "outputs" / "blocks"
SCEN = ["census", "godutch", "ebike", "local"]
CAPS = [1.15, 1.25, 1.5]
# Connectivity standards. all_ages (central): stress 1 links (protected lanes, paths, genuinely
# quiet streets) and crossings of stress <= 2 (signals, zebras); confident: stress <= 2 (painted
# lanes, moderately busy 50 km/h streets). Detours are judged on effort (metres + CLIMB_W x metres
# climbed), except confident_flat (distance only, the first version's measure).
LEVELS = {"all_ages": dict(lts=1, weight="eff"), "confident": dict(lts=2, weight="eff"),
          "confident_flat": dict(lts=2, weight="len")}
TOLS = list(LEVELS)
CAP0, TOL0, SCEN0 = 1.25, "all_ages", "godutch"
MIN_M = 200
ACCESS_M = 150
N_CORR, N_XING, MIN_DIR = 200, 80, 30
FILL_M = 150
GRID_M = 30
# Network bonus (a judgment, varied 0-1 in the draws): a project whose ends both join the existing
# cycle network scores (1 + GRID_BONUS) x its trips per $M, one end (1 + GRID_BONUS / 2).
GRID_BONUS = 0.5
CLIMB_W = 30.0  # metres of flat riding per metre climbed (the calibrated route-choice weight)
NODES_XY = None
BARRED = 9
ROADS_ALL = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
             "residential", "living_street", "road", "trunk", "trunk_link"}
GREEDY_STEPS, GREEDY_POOL = 10, 40
N_DRAWS = 1000
HEALTH_PER_KM, DAYS = 4.90, 250
QUIET_ROADS = {"residential", "unclassified", "tertiary", "tertiary_link", "living_street", "service", "road"}
COST = {"quiet": (0.1, 0.2, 0.3), "protected": (0.75, 1.6, 3.4), "protected_sh": (1.0, 2.0, 3.4),
        "signals": (0.5, 0.8, 1.5), "zebra": (0.15, 0.25, 0.4)}
# Minimum per project (design, junction works, a filter), so a 90 m link isn't nearly free.
MIN_COST = {"quiet": (0.03, 0.05, 0.1), "protected": (0.1, 0.25, 0.5), "protected_sh": (0.2, 0.4, 0.8)}
TREATMENT = {"quiet": "Quiet street (30 km/h + filter)", "protected": "Protected lane",
             "protected_sh": "Protected lane (state highway, NZTA)", "signals": "Signalised crossing",
             "zebra": "Raised zebra / refuge"}


def od_matrix():
    path = PROC / "cycle_od_matrix.npz"
    if path.exists():
        z = np.load(path)
        return z["S"], z["D"], z["mat"]
    M.run(M.load_params(), tuple(SCEN), verbose=False, collect_matrix=True)
    L = M.LAST_MATRIX
    assert L["scenarios"] == SCEN
    np.savez_compressed(path, S=L["S"], D=L["D"], mat=L["mat"])
    return L["S"], L["D"], L["mat"]


def arc_table(e):
    fw = pd.DataFrame({"u": e["u"], "v": e["v"], "edge": e.index, "dir": 0, "len": e["length_m"],
                       "up": e["up_fw"], "lts": e["lts_fw"], "cross": e["cross_v"], "ok": e["can_fw"]})
    bw = pd.DataFrame({"u": e["v"], "v": e["u"], "edge": e.index, "dir": 1, "len": e["length_m"],
                       "up": e["up_bw"], "lts": e["lts_bw"], "cross": e["cross_u"], "ok": e["can_bw"]})
    a = pd.concat([fw, bw], ignore_index=True)
    # Directions bikes may not ride today (one-way streets) stay in the table at stress 9, so a
    # council-plan treatment can open them (two-way cycleway); elsewhere they are never used.
    roads = e["highway"].isin(ROADS_ALL).values
    a = a[a["ok"] | np.concatenate([roads, roads])].reset_index(drop=True)
    a.loc[~a["ok"], "lts"] = BARRED
    a = a.drop(columns="ok")
    a["len"] = a["len"].clip(lower=0.5)
    a["eff"] = a["len"] + CLIMB_W * a["up"].fillna(0)
    return a


def low_stress(a, level, lts=None, cross=None, access=None):
    lts = a["lts"].values if lts is None else lts
    cross = a["cross"].values if cross is None else cross
    ok = (lts <= LEVELS[level]["lts"]) & (cross <= 2)
    if access is not None:
        ok |= access
    return ok


def csr(a, mask, n, weight="len"):
    s = a.loc[mask, ["u", "v", weight]].sort_values(weight).drop_duplicates(["u", "v"])
    return csr_matrix((s[weight].values, (s["u"].values, s["v"].values)), shape=(n, n))


class Connectivity:
    """Low-stress distances (or efforts) from every origin, and the trips connected, for one
    connectivity standard (LEVELS)."""

    def __init__(self, a, n, S, P, w, d_full, tol):
        self.a, self.n, self.S, self.P, self.w, self.tol = a, n, S, P, w, tol
        self.wt = LEVELS[tol]["weight"]
        self.d_full = d_full[self.wt]
        self.lts, self.cross = a["lts"].values.copy(), a["cross"].values.copy()
        # Access legs: the first arc out of an origin and the last into a destination may be any
        # stress up to ACCESS_M (homes and workplaces on arterials are reached along the kerb).
        self.access = ((np.isin(a["u"].values, S) | np.isin(a["v"].values, P["t"]))
                       & (a["len"].values <= ACCESS_M) & (a["lts"].values < BARRED))
        self.refresh()

    def refresh(self):
        self.mask = low_stress(self.a, self.tol, self.lts, self.cross, self.access)
        self.G = csr(self.a, self.mask, self.n, self.wt)
        self.dS = dijkstra(self.G, indices=self.S).astype(np.float32)  # S x N
        self.d = self.dS[self.P["si"], self.P["t"]]

    def connected(self, d):
        return np.stack([d <= c * self.d_full for c in CAPS])  # caps x pairs

    def totals(self, d=None):
        conn = self.connected(self.d if d is None else d)
        return conn.astype(np.float64) @ self.w  # caps x scenarios

    def with_arcs(self, idx_l, idx_c):
        """Low-stress distances for all pairs after treating arcs idx_l (protected lane or filtered
        quiet street: stress 1) and idx_c (crossing at their end node -> signals/zebra, level 2)."""
        lts, cross = self.lts.copy(), self.cross.copy()
        lts[idx_l], cross[idx_c] = np.minimum(lts[idx_l], 1), np.minimum(cross[idx_c], 2)
        idx = np.union1d(idx_l, idx_c).astype(int)
        mask = low_stress(self.a, self.tol, lts, cross, self.access)
        added = idx[mask[idx] & ~self.mask[idx]]
        if len(added) == 0:
            return self.d, lts, cross
        tails = np.unique(self.a["u"].values[added])
        Ga = csr(self.a, mask, self.n, self.wt)
        dA = dijkstra(Ga, indices=tails).astype(np.float32)  # A x N
        new = self.d.copy()
        si, t = self.P["si"], self.P["t"]
        for k, a in enumerate(tails):
            np.minimum(new, self.dS[si, a] + dA[k, t], out=new)
        return new, lts, cross

    def gain(self, idx_l, idx_c):
        new, _, _ = self.with_arcs(idx_l, idx_c)
        base = self.connected(self.d)
        after = self.connected(new)
        gained = (after & ~base).astype(np.float64)
        return gained @ self.w, gained @ (self.w * self.P["km"][:, None])  # caps x scen (trips, km)

    def apply(self, idx_l, idx_c):
        _, self.lts, self.cross = self.with_arcs(idx_l, idx_c)
        self.refresh()


def corridor_row(a, arc_key, e, ge, r, source, grid_xy, gap_index):
    """Treatment, cost and treated arcs for a run of same-street edges ge."""
    keys = [(i, 0) for i in ge.index[ge["hs_fw"]]] + [(i, 1) for i in ge.index[ge["hs_bw"]]]
    idx_l = arc_key.reindex(keys).dropna().astype(int).values
    # Internal junctions (nodes shared by two of the corridor's edges) get priority for riders
    # along the corridor; its end junctions are left as they are.
    nn = pd.Series(np.concatenate([ge["u"].values, ge["v"].values])).value_counts()
    inner = nn.index[nn >= 2].values
    idx_c = np.where(a["edge"].isin(ge.index).values & np.isin(a["v"].values, inner))[0]
    roads = set(ge["highway"])
    L = ge["length_m"]
    length = L.sum()
    one_way = (L * (ge["hs_fw"] != ge["hs_bw"]) * ge["can_fw"] * ge["can_bw"]).sum() / length
    max_adt = ge["adt"].max()
    if r["road"] == "State highway":
        kind = "protected_sh"
    elif roads <= QUIET_ROADS and max_adt <= 3000 and ge["speed"].max() <= 50:
        kind = "quiet"
    else:
        kind = "protected"
    f = 1 - 0.4 * one_way if kind != "quiet" else 1.0
    cost = [max(x * length / 1000 * f, m) for x, m in zip(COST[kind], MIN_COST[kind])]
    # Does it join the existing low-stress cycle network (protected lanes, tracks, paths)? Count the
    # corridor's ends (nodes used by one of its edges) within GRID_M of that network.
    ends = nn.index[nn == 1].values
    grid_ends = int(min(2, (grid_xy.query(NODES_XY[ends], distance_upper_bound=GRID_M)[0] <= GRID_M).sum())) \
        if len(ends) else 0
    return dict(type="corridor", source=source, name=r["street"], where=r["suburbs"], treatment=TREATMENT[kind],
                kind=kind, length_m=length, filled_m=L.drop(gap_index, errors="ignore").sum(),
                facility_now=r["facility_now"], max_adt=max_adt, speed=r["speed"], one_way_share=one_way,
                plan=r["plan"], gap_rank=r["rank"], cost_low=cost[0], cost=cost[1], cost_high=cost[2],
                grid_ends=grid_ends, idx_l=idx_l, idx_c=idx_c), ge.geometry.union_all()


def candidates(a):
    from scipy.spatial import cKDTree
    global NODES_XY
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    NODES_XY = nodes[["x", "y"]].values
    e_all, e = CG.load()
    # The existing low-stress cycle network: protected facilities, cycleways, living streets and
    # pedestrian areas bikes may use (e.g. the waterfront's Lady Elizabeth Lane), rated level 1-2
    # (points every 10 m).
    grid = e_all[(e_all["facility"].isin(CG.PROTECTED) | e_all["highway"].isin(["cycleway", "living_street",
                                                                                 "pedestrian"]))
                 & (e_all["lts"] <= 2)]
    gp = grid.geometry.segmentize(10).get_coordinates().values
    grid_xy = cKDTree(gp)
    # Under the all-ages standard a road direction is a gap at stress 2 too (painted lanes,
    # moderately busy streets), not only 3-4.
    road = e["highway"].isin(ROADS_ALL)
    for d in ("fw", "bw"):
        e[f"hs_{d}"] = e[f"can_{d}"] & (e[f"lts_{d}"] >= 2) & road
    for sc in CG.SCEN:
        e[f"hs_flow_{sc}"] = e[f"flow_{sc}_fw"] * e["hs_fw"] + e[f"flow_{sc}_bw"] * e["hs_bw"]
    e["hs_fac"] = np.where(e["hs_fw"], e["fac_fw"], e["fac_bw"])
    for d in ("fw", "bw"):
        e[f"gapd_{d}"] = e[f"hs_{d}"] & (e[f"flow_godutch_{d}"] >= MIN_DIR)
    e["gap"] = e["gapd_fw"] | e["gapd_bw"]
    two_way = e["can_fw"] & e["can_bw"]
    e["gap_dir"] = np.where(~e["gap"], "", np.where(e["gapd_fw"] & e["gapd_bw"] | ~two_way, "both", "one way"))
    c, g = CG.corridors(e)
    c = c.head(N_CORR)
    arc_key = pd.Series(np.arange(len(a)), index=pd.MultiIndex.from_arrays([a["edge"], a["dir"]]))
    rows, geoms, used = [], [], set()
    street = e["name"].fillna("(unnamed link)")
    busy = e[e["hs_fw"] | e["hs_bw"]]
    mid = gpd.GeoSeries(busy.geometry.interpolate(0.5, normalized=True), index=busy.index, crs=2193)
    for cid, r in c.iterrows():
        gap = g[g["corridor"] == cid]
        # Treat the whole busy stretch, not only the edges with enough potential trips: fill in the
        # same street's level 3-4 edges within FILL_M of the gap edges (short low-flow pieces
        # between gaps, which would otherwise leave the treated route broken).
        near = mid[(street.loc[mid.index] == r["street"]).values]
        near = near[near.within(gap.buffer(FILL_M).union_all())]
        ge = e.loc[gap.index.union(near.index)]
        row, geom = corridor_row(a, arc_key, e, ge, r, "gap", grid_xy, gap.index)
        used.update(ge.index)
        rows.append(row)
        geoms.append(geom)
    # Council plan: every unbuilt link of the Strategic Bike Network with a busy direction (e.g.
    # Bunny Street), whatever its modelled trips, unless a gap corridor above already covers it.
    ep = e.copy()
    # A one-way street on the plan becomes two-way for bikes (contraflow): its barred direction
    # counts as needing treatment.
    road = ep["highway"].isin(ROADS_ALL)
    for d in ("fw", "bw"):
        ep[f"hs_{d}"] = ep[f"hs_{d}"] | (road & ~ep[f"can_{d}"])
    ep["gap"] = ep["plan"].isin(["Planned (WCC)", "Unfunded (ex-LGWM)", "Desired only"]) & (ep["hs_fw"] | ep["hs_bw"])
    ep["gapd_fw"], ep["gapd_bw"] = ep["gap"] & ep["hs_fw"], ep["gap"] & ep["hs_bw"]
    ep["gap_dir"] = np.where(~ep["gap"], "", np.where(ep["gapd_fw"] & ep["gapd_bw"] | ~two_way, "both", "one way"))
    cp, gp_ = CG.corridors(ep)
    n_plan = 0
    for cid, r in cp.iterrows():
        ge = gp_[gp_["corridor"] == cid]
        if ge["length_m"][ge.index.isin(list(used))].sum() >= 0.5 * ge["length_m"].sum():
            continue
        ge = ep.loc[ge.index.difference(list(used))]
        if ge["length_m"].sum() < 30:
            continue
        row, geom = corridor_row(a, arc_key, e, ge, r, "council plan", grid_xy, ge.index)
        row["contraflow_m"] = ge["length_m"][(road & ~(e["can_fw"] & e["can_bw"])).reindex(ge.index)].sum()
        row["gap_rank"] = np.nan
        rows.append(row)
        geoms.append(geom)
        n_plan += 1
    print(f"council plan links added as candidates: {n_plan}")
    x = CG.crossings(e_all, e).head(N_XING)
    for n, r in x.iterrows():
        idx_c = np.where((a["v"].values == n) & (a["cross"].values >= 3) & (a["lts"].values <= 2))[0]
        kind = "signals" if (r["adt"] > 8000 or r["speed"] > 50) else "zebra"
        plan = e.loc[(e["u"] == n) | (e["v"] == n), "plan"]
        near_grid = grid_xy.query(NODES_XY[n], distance_upper_bound=GRID_M)[0] <= GRID_M
        rows.append(dict(type="crossing", source="crossing", name=f"{r['crossing']} at {r['approach']}",
                         where=r["suburb"], treatment=TREATMENT[kind], kind=kind, length_m=0.0,
                         facility_now="unsignalised", max_adt=r["adt"], speed=r["speed"], one_way_share=0.0,
                         plan=plan.mode().iat[0] if len(plan) else "Not in plan", gap_rank=r["rank"],
                         cost_low=COST[kind][0], cost=COST[kind][1], cost_high=COST[kind][2],
                         grid_ends=int(near_grid), idx_l=np.array([], dtype=int), idx_c=idx_c))
        geoms.append(gpd.points_from_xy([nodes.at[n, "x"]], [nodes.at[n, "y"]])[0])
    cand = gpd.GeoDataFrame(rows, geometry=geoms, crs=2193)
    cand = cand[(cand["idx_l"].map(len) + cand["idx_c"].map(len)) > 0].reset_index(drop=True)
    return cand, e


def crash_flags(cand):
    cr = gpd.read_file(RAW / "cycling" / "crashes.geojson").set_crs(2193, allow_override=True)
    sev = cr["crashSeverity"].isin(["Serious Crash", "Fatal Crash"])
    buf = gpd.GeoDataFrame(geometry=cand.buffer(30), crs=2193)
    j = gpd.sjoin(buf, gpd.GeoDataFrame({"sev": sev}, geometry=cr.geometry, crs=2193), predicate="intersects")
    g = j.groupby(level=0)["sev"].agg(["size", "sum"])
    cand["crashes"] = g["size"].reindex(cand.index).fillna(0).astype(int)
    cand["crashes_serious"] = g["sum"].reindex(cand.index).fillna(0).astype(int)
    return cand


def main():
    t0 = time.time()
    e = gpd.read_parquet(PROC / "cycle_edges.parquet")
    n = len(pd.read_parquet(PROC / "cycle_nodes.parquet"))
    a = arc_table(e)
    S, D, mat = od_matrix()
    print(f"trip matrix: {len(S):,} origins x {len(D):,} destinations; outbound trips/weekday "
          + ", ".join(f"{s} {mat[:, :, i].sum():,.0f}" for i, s in enumerate(SCEN)))
    allowed = a["lts"].values < BARRED
    d_full = dijkstra(csr(a, allowed, n), indices=S)[:, D].astype(np.float32)
    si, ti = np.nonzero(mat.sum(axis=2) > 0)
    df = d_full[si, ti]
    ok = np.isfinite(df) & (df >= MIN_M)
    si, ti, df = si[ok], ti[ok], df[ok]
    del d_full
    e_full = dijkstra(csr(a, allowed, n, "eff"), indices=S)[:, D].astype(np.float32)[si, ti]
    full = dict(len=df, eff=e_full)
    w = mat[si, ti].astype(np.float64)
    P = dict(si=si, t=D[ti], km=df / 1000)
    print(f"pairs: {len(si):,}; trips covered: " + ", ".join(f"{s} {w[:, i].sum():,.0f}" for i, s in enumerate(SCEN)))

    cand, e_city = candidates(a)
    cand = crash_flags(cand)
    print(f"candidates: {(cand['type'] == 'corridor').sum()} corridors, {(cand['type'] == 'crossing').sum()} crossings "
          f"({time.time() - t0:.0f}s)")

    # Connectivity today, and single-step gains for every candidate under each tolerance.
    conn = {tol: Connectivity(a, n, S, P, w, full, tol) for tol in TOLS}
    rows = []
    for tol, C in conn.items():
        tot = C.totals()
        for i, cap in enumerate(CAPS):
            for j, s in enumerate(SCEN):
                rows.append(dict(level=tol, cap=cap, scenario=s, trips=w[:, j].sum(), connected=tot[i, j],
                                 connected_pct=tot[i, j] / w[:, j].sum() * 100))
    base = pd.DataFrame(rows)
    print(base.round(1).to_string(index=False))
    gains = np.zeros((len(cand), len(TOLS), len(CAPS), len(SCEN)))
    gains_km = np.zeros_like(gains)
    for k, r in cand.iterrows():
        for ti_, tol in enumerate(TOLS):
            gains[k, ti_], gains_km[k, ti_] = conn[tol].gain(r["idx_l"], r["idx_c"])
        if k % 25 == 0:
            print(f"  evaluated {k + 1}/{len(cand)} ({time.time() - t0:.0f}s)", flush=True)
    i0, c0, s0 = TOLS.index(TOL0), CAPS.index(CAP0), SCEN.index(SCEN0)
    tot0 = base.set_index(["level", "cap", "scenario"])
    for s in SCEN:
        j = SCEN.index(s)
        cand[f"dT_{s}"] = gains[:, i0, c0, j]
        cand[f"dK_{s}"] = gains_km[:, i0, c0, j]
    cand["dT_pct_pts"] = cand["dT_godutch"] / tot0.loc[(TOL0, CAP0, SCEN0), "trips"] * 100
    cand["dT_per_M"] = cand["dT_godutch"] / cand["cost"]
    bonus = lambda b: 1 + b * cand["grid_ends"].values / 2  # noqa: E731
    cand["score"] = cand["dT_per_M"] * bonus(GRID_BONUS)
    cand["rank_trips"] = cand["dT_godutch"].rank(ascending=False, method="min").astype(int)
    # Extra cycling on newly connected trips, valued at the MBCM health rate (indicative).
    for lab, s in (("low", "local"), ("high", "godutch")):
        extra_km = cand[f"dK_{s}"] - cand["dK_census"]
        cand[f"health_M_{lab}"] = extra_km.clip(lower=0) * 2 * DAYS * HEALTH_PER_KM / 1e6

    # Monte Carlo single-step ranking.
    rng = np.random.default_rng(1)
    ranks = np.zeros((N_DRAWS, len(cand)))
    for b in range(N_DRAWS):
        ci, sj = rng.integers(len(CAPS)), rng.integers(len(SCEN))
        cost = rng.uniform(cand["cost_low"], cand["cost_high"])
        score = gains[:, i0, ci, sj] / cost * bonus(rng.uniform(0, 1))
        ranks[b] = pd.Series(score).rank(ascending=False, method="min").values
    cand["rank_median"] = np.median(ranks, axis=0)
    cand["rank_p10"] = np.percentile(ranks, 10, axis=0)
    cand["rank_p90"] = np.percentile(ranks, 90, axis=0)
    cand["top10_share"] = (ranks <= 10).mean(axis=0)
    cand["robust_top10"] = cand["top10_share"] >= 0.8
    # Standard sensitivity: judged for confident riders (or on distance only), which of the top 10
    # stay top 10?
    top = set(cand["score"].rank(ascending=False, method="first").loc[lambda x: x <= 10].index)
    for ti_, tol in enumerate(TOLS):
        if tol == TOL0:
            continue
        r = pd.Series(gains[:, ti_, c0, s0] / cand["cost"] * bonus(GRID_BONUS)).rank(ascending=False, method="first")
        cand[f"rank_{tol}"] = r.values
        print(f"{tol}: {len(top & set(r[r <= 10].index))}/10 of the top 10 stay top 10; "
              f"connected {tot0.loc[(tol, CAP0, SCEN0), 'connected_pct']:.1f}%")

    # Greedy build order (central case), re-evaluating the best pool each step.
    C = conn[TOL0]
    order, remaining = [], list(cand.sort_values("score", ascending=False).index)
    grid_f = bonus(GRID_BONUS)
    tot_now = C.totals()[c0, s0]
    steps = [dict(step=0, name="Today", connected=tot_now, connected_pct=tot_now / w[:, s0].sum() * 100,
                  cum_cost=0.0)]
    cum = 0.0
    for step in range(1, GREEDY_STEPS + 1):
        pool = remaining[:GREEDY_POOL]
        best, best_score, best_gain = None, -1, 0
        for k in pool:
            g, _ = C.gain(cand.at[k, "idx_l"], cand.at[k, "idx_c"])
            sc = g[c0, s0] / cand.at[k, "cost"] * grid_f[k]
            if sc > best_score:
                best, best_score, best_gain = k, sc, g[c0, s0]
        C.apply(cand.at[best, "idx_l"], cand.at[best, "idx_c"])
        remaining.remove(best)
        cum += cand.at[best, "cost"]
        tot_now = C.totals()[c0, s0]
        order.append(best)
        steps.append(dict(step=step, name=cand.at[best, "name"], treatment=cand.at[best, "treatment"],
                          cost=cand.at[best, "cost"], cum_cost=cum, dT=best_gain,
                          dT_per_M=best_gain / cand.at[best, "cost"], score=best_score,
                          grid_ends=cand.at[best, "grid_ends"],
                          connected=tot_now, connected_pct=tot_now / w[:, s0].sum() * 100))
        print(f"  step {step}: {cand.at[best, 'name']} ({cand.at[best, 'treatment']}) +{best_gain:,.0f} trips, "
              f"{tot_now / w[:, s0].sum() * 100:.1f}% connected, ${cum:.1f}M ({time.time() - t0:.0f}s)", flush=True)
    cand["build_step"] = pd.Series({k: i + 1 for i, k in enumerate(order)}).reindex(cand.index)

    cand = cand.sort_values("score", ascending=False).reset_index(drop=True)
    cand["rank"] = np.arange(1, len(cand) + 1)
    cols = ["rank", "rank_trips", "type", "source", "name", "where", "treatment", "length_m", "filled_m", "contraflow_m", "cost_low", "cost", "cost_high",
            "dT_godutch", "dT_ebike", "dT_local", "dT_census", "dK_godutch", "dT_pct_pts", "dT_per_M",
            "grid_ends", "score", "health_M_low", "health_M_high", "crashes", "crashes_serious", "plan", "facility_now", "max_adt",
            "speed", "one_way_share", "gap_rank", "rank_median", "rank_p10", "rank_p90", "top10_share",
            "robust_top10", "rank_confident", "rank_confident_flat", "build_step"]
    cand[cols].round(3).to_csv(TABLES / "cycle_priorities.csv", index=False)
    pd.DataFrame(steps).round(3).to_csv(TABLES / "cycle_build_order.csv", index=False)
    base.round(3).to_csv(TABLES / "cycle_connectivity.csv", index=False)
    out = cand[cols + ["geometry"]].copy()
    out["geometry"] = out.geometry.simplify(3)
    out.to_crs(4326).to_file(BLOCKS / "cycle_priorities.geojson", driver="GeoJSON")
    pd.set_option("display.width", 250)
    print(cand.head(25)[["rank", "name", "treatment", "length_m", "cost", "dT_godutch", "dT_local", "dT_per_M",
                         "grid_ends", "source", "plan", "rank_median", "top10_share", "build_step"]].round(2).to_string())
    print(f"robust top 10: {cand.loc[cand['robust_top10'], 'name'].tolist()}")
    print(json.dumps(steps[-1], default=float))


if __name__ == "__main__":
    main()
