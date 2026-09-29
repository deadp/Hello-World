"""Prioritise cycle network improvements for Wellington City: which links and crossings, in what
order, connect the most potential trips per dollar.

1. Trip table. cycle_model.run(collect_matrix=True) gives outbound trips by scenario (census,
   Go Dutch, e-bike, Wellington habits) from each home node to each destination node (work,
   education, shopping, leisure, visiting). Cached in data/processed/cycle_od_matrix.npz.
2. Connectivity (Mekuria, Furth & Nixon 2012; Furth, Mekuria & Nixon 2016; Lowry et al. 2016).
   The low-stress network is every arc with traffic stress <= 2 that ends at a crossing of stress
   <= 2. A trip is connected when its shortest low-stress route is at most `cap` x its shortest
   route on the whole network (cap 1.25 central; 1.15 and 1.5 as sensitivities). Trips under
   200 m are left out. The first and last arc of a trip (up to 150 m) may be any stress: homes
   and workplaces on arterials are reached along the kerb or footpath (without this allowance,
   only 4.5% of Go Dutch trips are connected, mostly because endpoints snap to arterial nodes).
   Crossing stress is per junction, not per turn, so a left turn onto a busy road counts as a
   crossing (conservative; ignoring crossings adds ~2-4 points). Tolerance variant: level 3 arcs (and level 3 crossings) shorter than
   150 m or 400 m count as low stress, an arc-level stand-in for Lowry's "up to ~150 m of LTS 3
   per route".
3. Candidates.
   Corridors: runs of the same street where a direction is level 3-4 and carries >= 30 Go Dutch
   trips a weekday (cycle_gaps.corridors with a lower threshold), top 150 by potential cycle-km.
   Treatment: residential/unclassified/tertiary streets with <= 3,000 vehicles/day get a quiet
   street (30 km/h and a modal filter); other roads a protected lane. Either way the treated
   directions become low stress, and so do the corridor's internal junctions. Crossings: the 80 busiest junctions where
   low-stress approaches meet an unsignalised level 3-4 road (cycle_gaps.crossings). Treatment:
   signals where the road carries > 8,000 vehicles/day or is faster than 50 km/h, otherwise a
   raised zebra or refuge.
4. Marginal benefit, exact. Adding a candidate's arcs A to the low-stress graph, the new
   low-stress distance is  d'(s,t) = min(d(s,t), min over tails a of A: d(s,a) + d_aug(a,t)),
   since any new route first joins a new arc at its tail. dT = trips newly connected per weekday
   (outbound; returns roughly double it), dK = their cycle-km.
5. Build order: greedy, adding the candidate with the largest dT per $M (Go Dutch, cap 1.25) and
   re-evaluating (links complement each other: a crossing can unlock a quiet street).
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
   Wellington habits/census) and cost (uniform in each range). Each draw ranks candidates by dT
   per $M (single-step); report median rank, 10-90% band and the share of draws in the top 10
   ("robust" if >= 80%). The level-3 tolerance is reported separately (rank_tol150, rank_tol400):
   it redefines the problem (forgiving short busy stretches connects most short gaps outright),
   so mixing it into the draws only reshuffles the short links.

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
TOLS = [0.0, 150.0, 400.0]
CAP0, TOL0, SCEN0 = 1.25, 0.0, "godutch"
MIN_M = 200
ACCESS_M = 150
N_CORR, N_XING, MIN_DIR = 150, 80, 30
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
                       "lts": e["lts_fw"], "cross": e["cross_v"], "ok": e["can_fw"]})
    bw = pd.DataFrame({"u": e["v"], "v": e["u"], "edge": e.index, "dir": 1, "len": e["length_m"],
                       "lts": e["lts_bw"], "cross": e["cross_u"], "ok": e["can_bw"]})
    a = pd.concat([fw, bw], ignore_index=True)
    a = a[a["ok"]].drop(columns="ok").reset_index(drop=True)
    a["len"] = a["len"].clip(lower=0.5)
    return a


def low_stress(a, tol, lts=None, cross=None, access=None):
    lts = a["lts"].values if lts is None else lts
    cross = a["cross"].values if cross is None else cross
    ok = (lts <= 2) & (cross <= 2)
    if access is not None:
        ok |= access
    if tol > 0:
        ok |= (lts <= 3) & (cross <= 3) & (a["len"].values <= tol)
    return ok


def csr(a, mask, n):
    s = a.loc[mask, ["u", "v", "len"]].sort_values("len").drop_duplicates(["u", "v"])
    return csr_matrix((s["len"].values, (s["u"].values, s["v"].values)), shape=(n, n))


class Connectivity:
    """Low-stress distances from every origin, and the trips connected, for one tolerance."""

    def __init__(self, a, n, S, P, w, d_full, tol):
        self.a, self.n, self.S, self.P, self.w, self.d_full, self.tol = a, n, S, P, w, d_full, tol
        self.lts, self.cross = a["lts"].values.copy(), a["cross"].values.copy()
        # Access legs: the first arc out of an origin and the last into a destination may be any
        # stress up to ACCESS_M (homes and workplaces on arterials are reached along the kerb).
        self.access = ((np.isin(a["u"].values, S) | np.isin(a["v"].values, P["t"]))
                       & (a["len"].values <= ACCESS_M))
        self.refresh()

    def refresh(self):
        self.mask = low_stress(self.a, self.tol, self.lts, self.cross, self.access)
        self.G = csr(self.a, self.mask, self.n)
        self.dS = dijkstra(self.G, indices=self.S).astype(np.float32)  # S x N
        self.d = self.dS[self.P["si"], self.P["t"]]

    def connected(self, d):
        return np.stack([d <= c * self.d_full for c in CAPS])  # caps x pairs

    def totals(self, d=None):
        conn = self.connected(self.d if d is None else d)
        return conn.astype(np.float64) @ self.w  # caps x scenarios

    def with_arcs(self, idx_l, idx_c):
        """Low-stress distances for all pairs after treating arcs idx_l (stress -> at most level 2
        along the link) and idx_c (crossing at their end node -> at most level 2)."""
        lts, cross = self.lts.copy(), self.cross.copy()
        lts[idx_l], cross[idx_c] = np.minimum(lts[idx_l], 2), np.minimum(cross[idx_c], 2)
        idx = np.union1d(idx_l, idx_c).astype(int)
        mask = low_stress(self.a, self.tol, lts, cross, self.access)
        added = idx[mask[idx] & ~self.mask[idx]]
        if len(added) == 0:
            return self.d, lts, cross
        tails = np.unique(self.a["u"].values[added])
        Ga = csr(self.a, mask, self.n)
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


def candidates(a):
    e_all, e = CG.load()
    for d in ("fw", "bw"):
        e[f"gapd_{d}"] = e[f"hs_{d}"] & (e[f"flow_godutch_{d}"] >= MIN_DIR)
    e["gap"] = e["gapd_fw"] | e["gapd_bw"]
    two_way = e["can_fw"] & e["can_bw"]
    e["gap_dir"] = np.where(~e["gap"], "", np.where(e["gapd_fw"] & e["gapd_bw"] | ~two_way, "both", "one way"))
    c, g = CG.corridors(e)
    c = c.head(N_CORR)
    arc_key = pd.Series(np.arange(len(a)), index=pd.MultiIndex.from_arrays([a["edge"], a["dir"]]))
    rows, geoms = [], []
    for cid, r in c.iterrows():
        ge = g[g["corridor"] == cid]
        keys = [(i, 0) for i in ge.index[ge["hs_fw"]]] + [(i, 1) for i in ge.index[ge["hs_bw"]]]
        idx_l = arc_key.reindex(keys).dropna().astype(int).values
        # Internal junctions (nodes shared by two of the corridor's edges) get priority for riders
        # along the corridor; its end junctions are left as they are.
        nn = pd.Series(np.concatenate([ge["u"].values, ge["v"].values])).value_counts()
        inner = nn.index[nn >= 2].values
        idx_c = np.where(a["edge"].isin(ge.index).values & np.isin(a["v"].values, inner))[0]
        roads = set(ge["highway"])
        if r["road"] == "State highway":
            kind = "protected_sh"
        elif roads <= QUIET_ROADS and r["max_adt"] <= 3000 and ge["speed"].max() <= 50:
            kind = "quiet"
        else:
            kind = "protected"
        f = 1 - 0.4 * r["one_way_share"] if kind != "quiet" else 1.0
        cost = [max(x * r["length_m"] / 1000 * f, m) for x, m in zip(COST[kind], MIN_COST[kind])]
        rows.append(dict(type="corridor", name=r["street"], where=r["suburbs"], treatment=TREATMENT[kind],
                         kind=kind, length_m=r["length_m"], facility_now=r["facility_now"], max_adt=r["max_adt"],
                         speed=r["speed"], one_way_share=r["one_way_share"], plan=r["plan"],
                         gap_rank=r["rank"], cost_low=cost[0], cost=cost[1], cost_high=cost[2],
                         idx_l=idx_l, idx_c=idx_c))
        geoms.append(ge.geometry.union_all())
    x = CG.crossings(e_all, e).head(N_XING)
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    for n, r in x.iterrows():
        idx_c = np.where((a["v"].values == n) & (a["cross"].values >= 3) & (a["lts"].values <= 2))[0]
        kind = "signals" if (r["adt"] > 8000 or r["speed"] > 50) else "zebra"
        plan = e.loc[(e["u"] == n) | (e["v"] == n), "plan"]
        rows.append(dict(type="crossing", name=f"{r['crossing']} at {r['approach']}", where=r["suburb"],
                         treatment=TREATMENT[kind], kind=kind, length_m=0.0, facility_now="unsignalised",
                         max_adt=r["adt"], speed=r["speed"], one_way_share=0.0,
                         plan=plan.mode().iat[0] if len(plan) else "Not in plan", gap_rank=r["rank"],
                         cost_low=COST[kind][0], cost=COST[kind][1], cost_high=COST[kind][2],
                         idx_l=np.array([], dtype=int), idx_c=idx_c))
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
    d_full = dijkstra(csr(a, np.ones(len(a), bool), n), indices=S)[:, D].astype(np.float32)
    si, ti = np.nonzero(mat.sum(axis=2) > 0)
    df = d_full[si, ti]
    ok = np.isfinite(df) & (df >= MIN_M)
    si, ti, df = si[ok], ti[ok], df[ok]
    w = mat[si, ti].astype(np.float64)
    P = dict(si=si, t=D[ti], km=df / 1000)
    print(f"pairs: {len(si):,}; trips covered: " + ", ".join(f"{s} {w[:, i].sum():,.0f}" for i, s in enumerate(SCEN)))

    cand, e_city = candidates(a)
    cand = crash_flags(cand)
    print(f"candidates: {(cand['type'] == 'corridor').sum()} corridors, {(cand['type'] == 'crossing').sum()} crossings "
          f"({time.time() - t0:.0f}s)")

    # Connectivity today, and single-step gains for every candidate under each tolerance.
    conn = {tol: Connectivity(a, n, S, P, w, df, tol) for tol in TOLS}
    rows = []
    for tol, C in conn.items():
        tot = C.totals()
        for i, cap in enumerate(CAPS):
            for j, s in enumerate(SCEN):
                rows.append(dict(tolerance_m=tol, cap=cap, scenario=s, trips=w[:, j].sum(), connected=tot[i, j],
                                 connected_pct=tot[i, j] / w[:, j].sum() * 100))
    base = pd.DataFrame(rows)
    print(base[base["tolerance_m"] == 0].round(1).to_string(index=False))
    gains = np.zeros((len(cand), len(TOLS), len(CAPS), len(SCEN)))
    gains_km = np.zeros_like(gains)
    for k, r in cand.iterrows():
        for ti_, tol in enumerate(TOLS):
            gains[k, ti_], gains_km[k, ti_] = conn[tol].gain(r["idx_l"], r["idx_c"])
        if k % 25 == 0:
            print(f"  evaluated {k + 1}/{len(cand)} ({time.time() - t0:.0f}s)", flush=True)
    i0, c0, s0 = TOLS.index(TOL0), CAPS.index(CAP0), SCEN.index(SCEN0)
    tot0 = base.set_index(["tolerance_m", "cap", "scenario"])
    for s in SCEN:
        j = SCEN.index(s)
        cand[f"dT_{s}"] = gains[:, i0, c0, j]
        cand[f"dK_{s}"] = gains_km[:, i0, c0, j]
    cand["dT_pct_pts"] = cand["dT_godutch"] / tot0.loc[(TOL0, CAP0, SCEN0), "trips"] * 100
    cand["dT_per_M"] = cand["dT_godutch"] / cand["cost"]
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
        score = gains[:, i0, ci, sj] / cost
        ranks[b] = pd.Series(score).rank(ascending=False, method="min").values
    cand["rank_median"] = np.median(ranks, axis=0)
    cand["rank_p10"] = np.percentile(ranks, 10, axis=0)
    cand["rank_p90"] = np.percentile(ranks, 90, axis=0)
    cand["top10_share"] = (ranks <= 10).mean(axis=0)
    cand["robust_top10"] = cand["top10_share"] >= 0.8
    # Tolerance sensitivity: with short busy stretches forgiven, which of today's top 10 stay top 10?
    top = set(cand["dT_per_M"].rank(ascending=False, method="first").loc[lambda x: x <= 10].index)
    for ti_, tol in enumerate(TOLS):
        if tol == TOL0:
            continue
        r = pd.Series(gains[:, ti_, c0, s0] / cand["cost"]).rank(ascending=False, method="first")
        cand[f"rank_tol{int(tol)}"] = r.values
        print(f"tolerance {tol:.0f} m: {len(top & set(r[r <= 10].index))}/10 of the top 10 stay top 10; "
              f"connected {base.set_index(['tolerance_m', 'cap', 'scenario']).loc[(tol, CAP0, SCEN0), 'connected_pct']:.1f}%")

    # Greedy build order (central case), re-evaluating the best pool each step.
    C = conn[TOL0]
    order, remaining = [], list(cand.sort_values("dT_per_M", ascending=False).index)
    tot_now = C.totals()[c0, s0]
    steps = [dict(step=0, name="Today", connected=tot_now, connected_pct=tot_now / w[:, s0].sum() * 100,
                  cum_cost=0.0)]
    cum = 0.0
    for step in range(1, GREEDY_STEPS + 1):
        pool = remaining[:GREEDY_POOL]
        best, best_score, best_gain = None, -1, 0
        for k in pool:
            g, _ = C.gain(cand.at[k, "idx_l"], cand.at[k, "idx_c"])
            sc = g[c0, s0] / cand.at[k, "cost"]
            if sc > best_score:
                best, best_score, best_gain = k, sc, g[c0, s0]
        C.apply(cand.at[best, "idx_l"], cand.at[best, "idx_c"])
        remaining.remove(best)
        cum += cand.at[best, "cost"]
        tot_now = C.totals()[c0, s0]
        order.append(best)
        steps.append(dict(step=step, name=cand.at[best, "name"], treatment=cand.at[best, "treatment"],
                          cost=cand.at[best, "cost"], cum_cost=cum, dT=best_gain, dT_per_M=best_score,
                          connected=tot_now, connected_pct=tot_now / w[:, s0].sum() * 100))
        print(f"  step {step}: {cand.at[best, 'name']} ({cand.at[best, 'treatment']}) +{best_gain:,.0f} trips, "
              f"{tot_now / w[:, s0].sum() * 100:.1f}% connected, ${cum:.1f}M ({time.time() - t0:.0f}s)", flush=True)
    cand["build_step"] = pd.Series({k: i + 1 for i, k in enumerate(order)}).reindex(cand.index)

    cand = cand.sort_values("dT_per_M", ascending=False).reset_index(drop=True)
    cand["rank"] = np.arange(1, len(cand) + 1)
    cols = ["rank", "type", "name", "where", "treatment", "length_m", "cost_low", "cost", "cost_high",
            "dT_godutch", "dT_ebike", "dT_local", "dT_census", "dK_godutch", "dT_pct_pts", "dT_per_M",
            "health_M_low", "health_M_high", "crashes", "crashes_serious", "plan", "facility_now", "max_adt",
            "speed", "one_way_share", "gap_rank", "rank_median", "rank_p10", "rank_p90", "top10_share",
            "robust_top10", "rank_tol150", "rank_tol400", "build_step"]
    cand[cols].round(3).to_csv(TABLES / "cycle_priorities.csv", index=False)
    pd.DataFrame(steps).round(3).to_csv(TABLES / "cycle_build_order.csv", index=False)
    base.round(3).to_csv(TABLES / "cycle_connectivity.csv", index=False)
    out = cand[cols + ["geometry"]].copy()
    out["geometry"] = out.geometry.simplify(3)
    out.to_crs(4326).to_file(BLOCKS / "cycle_priorities.geojson", driver="GeoJSON")
    pd.set_option("display.width", 250)
    print(cand.head(25)[["rank", "name", "treatment", "length_m", "cost", "dT_godutch", "dT_local", "dT_per_M",
                         "crashes", "plan", "rank_median", "top10_share", "build_step"]].round(2).to_string())
    print(f"robust top 10: {cand.loc[cand['robust_top10'], 'name'].tolist()}")
    print(json.dumps(steps[-1], default=float))


if __name__ == "__main__":
    main()
