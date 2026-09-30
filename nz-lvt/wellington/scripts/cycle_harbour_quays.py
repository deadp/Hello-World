"""What would the Harbour Quays bus priority project mean for cycling in Wellington City?

The project (Metlink / Waka Kotahi project pages, researched 30 Sep 2026): a second bus spine from
Wellington Railway Station to Cambridge Terrace / Courtenay Place along Whitmore Street,
Customhouse Quay, Jervois Quay, Cable Street and Wakefield Street (also Kent and Cambridge
Terrace). Kerbside bus lanes on Customhouse Quay, Jervois Quay, Wakefield Street and Cable Street,
peak hours only (Mon-Fri 6:30-9:30am and 3:30-6:30pm). Bikes may use the bus lanes. Outside the
peaks the lanes are general traffic; two general traffic lanes each way stay. No cycle lanes are
announced. Traffic resolution approved 17 Sep 2026; construction mid-2027 to Dec 2027; about $11M.
We cannot see the designs, so every scenario below is an assumption about them.

1. Corridor. Road edges (not paths) named Whitmore Street, Customhouse Quay, Jervois Quay, Cable
   Street, Wakefield Street, Kent Terrace or Cambridge Terrace inside the CBD box (E 1748500-
   1749700, N 5426300-5429300, NZTM). The box drops same-named streets elsewhere (Wakefield Street
   in Miramar). Edges are one carriageway each on the dual carriageways (Customhouse/Jervois Quay).
2. Scenarios (each applied on top of today's network, cycle_edges.parquet):
   A  As announced. Riding in a kerbside bus lane with frequent buses is level 3 (confident riders
      only). Off peak the lane is mixed traffic. Every on-road direction of the four bus-lane
      streets is set to max(today, 3). Directions on a separate path or protected lane (sidepath,
      track, protected lane, shared path) are left alone: the bus lane does not change them, and
      riders keep using them. Junctions are unchanged. A can only match or lower a rating, and
      level 3 never counts as connected (all ages needs <= 1, confident <= 2), so the test is
      whether it worsens any direction now at level 1-2 (reported as edges, metres and trips).
   B  A plus protected lanes (level 1) on every road direction of the whole corridor (all seven
      streets), with signal priority at internal junctions (crossing <= 2). Internal = a node
      shared by two corridor edges. Directions already at level 1 stay as they are.
   C  B plus connectors that join the corridor's ends to protected routes, if not protected yet:
      - Bunny Street: joins the station end (Whitmore/Featherston, next to the Thorndon Quay
        cycleway) to Waterloo Quay's protected lane and Lady Elizabeth Lane (the council's own
        planned link, see cycle_priorities.py).
      - Featherston Street north of Customhouse Quay: the parallel link from the station end to
        the Whitmore/Customhouse junction and Bunny Street.
      - Taranaki Street from Wakefield Street to Courtenay Place: the only south-going route that
        leaves the corridor in the middle, toward Courtenay Place, Te Aro and the Mt Victoria /
        Newtown cycleways beyond.
      The Kent/Cambridge Terrace end needs no connector: it already meets the Newtown to City
      Cycleway, the SH1 cycleway and the Mt Victoria tunnel shared path at the Basin Reserve.
3. Effect on cycling: same trip table (cycle_od_matrix.npz, weekday outbound trips by scenario)
   and connectivity method as cycle_priorities.py: a trip is connected when its least-effort route
   on the low-stress network is at most 1.25 x (also 1.15 and 1.5) its least-effort route on the
   whole network, effort = metres + 30 x metres climbed, first and last arc (<= 150 m) any stress.
   "all_ages" = stress 1 links and crossings <= 2; "confident" = stress <= 2. Reported for Go
   Dutch and census (today's riders); e-bike and Wellington-habits trips are in the CSV too. The
   scenarios are recomputed in full (a Dijkstra from every origin), because A lowers ratings.
   Origin suburb = the Suburb of the nearest rateable parcel (units.parquet) to the origin node.
4. Cost, indicative only. Protected lane $0.75-3.4M per km, central $1.6M (as cycle_priorities.py),
   on the road directions that are not level 1 today. Per street-km: a two-way edge treated in
   both directions counts 1.0, one direction of a two-way edge 0.6 (the 1 - 0.4 factor used
   elsewhere), a one-way carriageway edge 0.5 (its pair is the other carriageway). Signal priority
   at internal junctions is costed separately: junctions whose crossing rating falls to 2, at
   $0.5-1.5M each (central 0.8), an upper bound as many probably have signals already. Sidepaths
   (shared footpaths rated level 1 in the network model) are assumed to stay as they are.
   Bus-lane cost is not counted: it is in the project's own $11M.
5. Feasibility: road reserve width (reserve_m, boundary to boundary) per street, median and min.
   Not every edge has a value. Rough arithmetic, not a design: two 3.2 m traffic lanes each way
   (12.8 m), two 2.5 m protected lanes with buffers (5 m) and two 3 m footpaths (6 m) need about
   24 m before bus lanes take any of the general width, so a 20 m reserve is tight.
6. The waterfront shared path (cycleway/path edges within 120 m of Customhouse and Jervois Quay)
   is not touched by any scenario; its stress is reported for reference.

Writes outputs/tables/cycle_harbour_quays.csv (one row per scenario x level),
cycle_harbour_quays_suburbs.csv (origin suburbs), cycle_harbour_quays_streets.csv (reserve width
and cost by street), cycle_harbour_quays.md (summary) and outputs/blocks/cycle_harbour_quays.geojson
(WGS84: corridor and connector edges with scenario labels and stress before and after).
"""

import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

import cycle_priorities as CP

PROC, TABLES, BLOCKS = CP.PROC, CP.TABLES, CP.BLOCKS
SCEN, CAPS, CAP0 = CP.SCEN, CP.CAPS, CP.CAP0
LEVELS_USED = ["all_ages", "confident"]
BOX = (1748500, 5426300, 1749700, 5429300)
BUS_STREETS = ["Customhouse Quay", "Jervois Quay", "Wakefield Street", "Cable Street"]
CORRIDOR = ["Whitmore Street", "Customhouse Quay", "Jervois Quay", "Cable Street", "Wakefield Street",
            "Kent Terrace", "Cambridge Terrace"]
FEATHERSTON_N = 5428430  # Featherston Street north of the Whitmore / Customhouse Quay junction
TARANAKI_N = 5427050  # Taranaki Street from Wakefield Street to Courtenay Place
ON_ROAD = {"mixed", "sharrow", "painted_lane", "buffered_lane", "bus_lane"}
BUS_LTS = 3
COST = CP.COST["protected"]
JUNCTION_COST = CP.COST["signals"]
SCEN_LABEL = {"A": "A: bus lanes as announced", "B": "B: A + protected lanes and junction priority",
              "C": "C: B + connectors (Bunny St, Featherston St, Taranaki St)"}


def select_edges(e):
    c = e.geometry.centroid
    inbox = c.x.between(BOX[0], BOX[2]) & c.y.between(BOX[1], BOX[3])
    road = e["highway"].isin(CP.ROADS_ALL)
    corridor = inbox & road & e["name"].isin(CORRIDOR)
    bunny = inbox & road & (e["name"] == "Bunny Street")
    feath = inbox & road & (e["name"] == "Featherston Street") & (c.y >= FEATHERSTON_N)
    taran = inbox & road & (e["name"] == "Taranaki Street") & (c.y >= TARANAKI_N)
    role = pd.Series("", index=e.index)
    role[corridor] = "corridor"
    role[bunny] = "connector: Bunny Street"
    role[feath] = "connector: Featherston Street"
    role[taran] = "connector: Taranaki Street"
    return role[role != ""]


def build_lts(a, e, role):
    """Arc-level stress and crossing arrays for today and scenarios A, B, C."""
    arc_role = a["edge"].map(role)
    corr = (arc_role == "corridor").values
    conn = arc_role.fillna("").str.startswith("connector").values
    name = a["edge"].map(e["name"]).values
    fac = np.where(a["dir"].values == 0, e["fac_fw"].reindex(a["edge"]).values,
                   e["fac_bw"].reindex(a["edge"]).values)
    lts0, cross0 = a["lts"].values.copy(), a["cross"].values.copy()
    bus = corr & np.isin(name, BUS_STREETS) & np.isin(fac, list(ON_ROAD))
    ltsA = np.where(bus, np.maximum(lts0, BUS_LTS), lts0)
    # B: protected lanes on the whole corridor, priority at internal junctions.
    edges_c = e.loc[role[role == "corridor"].index]
    nn = pd.Series(np.concatenate([edges_c["u"].values, edges_c["v"].values])).value_counts()
    inner_c = nn.index[nn >= 2].values
    ltsB = np.where(corr, 1, ltsA)
    crossB = cross0.copy()
    jB = corr & np.isin(a["v"].values, inner_c)
    crossB[jB] = np.minimum(crossB[jB], 2)
    # C: connectors as well; internal junctions of the connector edges (shared by two of them).
    edges_k = e.loc[role[role.str.startswith("connector")].index]
    nk = pd.Series(np.concatenate([edges_k["u"].values, edges_k["v"].values])).value_counts()
    inner_k = nk.index[nk >= 2].values
    ltsC = np.where(conn, 1, ltsB)
    crossC = crossB.copy()
    jC = conn & np.isin(a["v"].values, inner_k)
    crossC[jC] = np.minimum(crossC[jC], 2)
    return dict(today=(lts0, cross0), A=(ltsA, cross0), B=(ltsB, crossB), C=(ltsC, crossC)), \
        dict(bus=bus, corr=corr, conn=conn, arc_role=arc_role)


def trips_by(conn_flags, w):
    return conn_flags.astype(np.float64) @ w  # caps x scenarios


def main():
    t0 = time.time()
    e = gpd.read_parquet(PROC / "cycle_edges.parquet")
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    n = len(nodes)
    a = CP.arc_table(e)
    role = select_edges(e)
    print("edges selected:", role.value_counts().to_dict(), f"{e.loc[role.index, 'length_m'].sum():,.0f} m")
    lts_sc, flags = build_lts(a, e, role)

    S, D, mat = CP.od_matrix()
    allowed = a["lts"].values < CP.BARRED
    d_full = dijkstra(CP.csr(a, allowed, n), indices=S)[:, D].astype(np.float32)
    si, ti = np.nonzero(mat.sum(axis=2) > 0)
    df = d_full[si, ti]
    ok = np.isfinite(df) & (df >= CP.MIN_M)
    si, ti, df = si[ok], ti[ok], df[ok]
    del d_full
    e_full = dijkstra(CP.csr(a, allowed, n, "eff"), indices=S)[:, D].astype(np.float32)[si, ti]
    full = dict(len=df, eff=e_full)
    w = mat[si, ti].astype(np.float64)
    del mat
    P = dict(si=si, t=D[ti], km=df / 1000)
    print(f"pairs {len(si):,}; trips " + ", ".join(f"{s} {w[:, i].sum():,.0f}" for i, s in enumerate(SCEN))
          + f" ({time.time() - t0:.0f}s)", flush=True)

    # Suburb of each origin node.
    units = gpd.read_parquet(PROC / "units.parquet", columns=["Suburb", "geometry"])
    uc = units.geometry.centroid
    tree = cKDTree(np.c_[uc.x.values, uc.y.values])
    sub = units["Suburb"].values[tree.query(nodes[["x", "y"]].values[S])[1]]
    del units, uc

    rows, sub_rows = [], []
    changes = {}  # arc-level connected flags kept for the worsening check
    for tol in LEVELS_USED:
        C = CP.Connectivity(a, n, S, P, w, full, tol)
        C.lts, C.cross = lts_sc["today"][0].copy(), lts_sc["today"][1].copy()
        C.refresh()
        base = C.connected(C.d)  # caps x pairs
        base_tot = trips_by(base, w)
        tot_trips = w.sum(axis=0)
        for sc in ("A", "B", "C"):
            C.lts, C.cross = lts_sc[sc][0].copy(), lts_sc[sc][1].copy()
            C.refresh()
            after = C.connected(C.d)
            tot = trips_by(after, w)
            gain_pairs = (after.astype(np.float64) - base.astype(np.float64))  # caps x pairs (+1 / -1)
            row = dict(scenario=SCEN_LABEL[sc], code=sc, level=tol)
            for j, s in enumerate(SCEN):
                i0 = CAPS.index(CAP0)
                row[f"trips_{s}"] = tot_trips[j]
                row[f"connected_today_{s}"] = base_tot[i0, j]
                row[f"connected_after_{s}"] = tot[i0, j]
                row[f"newly_connected_{s}"] = tot[i0, j] - base_tot[i0, j]
                row[f"pct_today_{s}"] = base_tot[i0, j] / tot_trips[j] * 100
                row[f"pct_after_{s}"] = tot[i0, j] / tot_trips[j] * 100
                row[f"pct_change_{s}"] = row[f"pct_after_{s}"] - row[f"pct_today_{s}"]
            for cap in (1.15, 1.5):
                i = CAPS.index(cap)
                row[f"newly_connected_godutch_cap{cap}"] = tot[i, 1] - base_tot[i, 1]
                row[f"newly_connected_census_cap{cap}"] = tot[i, 0] - base_tot[i, 0]
            # Origin suburbs (Go Dutch and census, central cap).
            i0 = CAPS.index(CAP0)
            g = pd.DataFrame({"suburb": sub[si], "gd": gain_pairs[i0] * w[:, 1], "census": gain_pairs[i0] * w[:, 0],
                              "gd_all": w[:, 1]}).groupby("suburb").sum()
            g["gd_pct_pts"] = g["gd"] / g["gd_all"] * 100
            top = g.sort_values("gd", ascending=False).head(6)
            row["top_suburbs_godutch"] = "; ".join(f"{k} {v:+,.0f}" for k, v in top["gd"].items() if abs(v) >= 0.5)
            topc = g.sort_values("census", ascending=False).head(5)
            row["top_suburbs_census"] = "; ".join(f"{k} {v:+,.1f}" for k, v in topc["census"].items() if abs(v) >= 0.05)
            for k, r in g.iterrows():
                sub_rows.append(dict(scenario=sc, level=tol, suburb=k, newly_connected_godutch=r["gd"],
                                     newly_connected_census=r["census"], godutch_trips_from_suburb=r["gd_all"],
                                     godutch_pct_pts=r["gd_pct_pts"]))
            if sc == "A":
                lost = gain_pairs[i0] < 0
                row["trips_lost_godutch"] = -(gain_pairs[i0][lost] * w[lost, 1]).sum()
            rows.append(row)
            print(f"  {tol} {sc}: godutch {row['newly_connected_godutch']:+,.0f} "
                  f"({row['pct_change_godutch']:+.2f} pts), census {row['newly_connected_census']:+,.1f} "
                  f"({time.time() - t0:.0f}s)", flush=True)
        del C

    # Cost and reserve width by street.
    ec = e.loc[role.index].copy()
    ec["role"] = role
    both = ec["can_fw"] & ec["can_bw"]
    need_fw = ec["can_fw"] & (ec["lts_fw"] > 1)
    need_bw = ec["can_bw"] & (ec["lts_bw"] > 1)
    ndir = need_fw.astype(int) + need_bw.astype(int)
    ec["km_eq"] = ec["length_m"] / 1000 * np.where(both, np.where(ndir == 2, 1.0, np.where(ndir == 1, 0.6, 0.0)),
                                                   0.5 * (ndir > 0))
    ec["treated_m"] = ec["length_m"] * (ndir > 0)
    # Internal-junction upgrades: arcs whose crossing rating drops to 2 (B/C).
    up = {}
    for sc, mask_key in (("B", "corr"), ("C", "conn")):
        m = flags[mask_key] & (lts_sc[sc][1] < lts_sc["today"][1])
        up[sc] = len(np.unique(a["v"].values[m]))
    streets = ec.groupby(["role", "name"]).agg(edges=("u", "size"), length_m=("length_m", "sum"),
                                                treated_m=("treated_m", "sum"), street_km_eq=("km_eq", "sum"),
                                                reserve_median_m=("reserve_m", "median"), reserve_min_m=("reserve_m", "min"),
                                                reserve_n=("reserve_m", "count")).reset_index()
    for k, lab in enumerate(("low", "central", "high")):
        streets[f"cost_{lab}_M"] = streets["street_km_eq"] * COST[k]
    streets.round(2).to_csv(TABLES / "cycle_harbour_quays_streets.csv", index=False)
    corr_km = ec.loc[ec["role"] == "corridor", "km_eq"].sum()
    conn_km = ec.loc[ec["role"] != "corridor", "km_eq"].sum()
    cost = {"A": (0, 0, 0), "B": tuple(corr_km * c for c in COST), "C": tuple((corr_km + conn_km) * c for c in COST)}
    jn = {"A": 0, "B": up["B"], "C": up["B"] + up["C"]}
    for r in rows:
        c = cost[r["code"]]
        r.update(protected_km_eq=dict(A=0, B=corr_km, C=corr_km + conn_km)[r["code"]],
                 cost_low_M=c[0], cost_central_M=c[1], cost_high_M=c[2], junctions_upgraded=jn[r["code"]],
                 junction_cost_central_M=jn[r["code"]] * JUNCTION_COST[1],
                 junction_cost_range_M=f"{jn[r['code']] * JUNCTION_COST[0]:.1f}-{jn[r['code']] * JUNCTION_COST[2]:.1f}")
        r["godutch_trips_per_M_central"] = (r["newly_connected_godutch"] / r["cost_central_M"]
                                            if r["cost_central_M"] > 0 else np.nan)
    out = pd.DataFrame(rows)
    out.drop(columns="code").round(3).to_csv(TABLES / "cycle_harbour_quays.csv", index=False)
    pd.DataFrame(sub_rows).round(3).to_csv(TABLES / "cycle_harbour_quays_suburbs.csv", index=False)

    # Scenario A: what changes per direction.
    lts0, ltsA = lts_sc["today"][0], lts_sc["A"][0]
    bus_arcs = flags["bus"]
    delta = pd.DataFrame({"edge": a["edge"], "before": lts0, "after": ltsA, "len": a["len"], "bus": bus_arcs})
    da = delta[delta["bus"]]
    a_worse = da[da["after"] > da["before"]]
    a_same = da[da["after"] == da["before"]]
    a_stats = dict(arcs=len(da), worse=len(a_worse), worse_m=a_worse["len"].sum(), same=len(a_same),
                   same_m=a_same["len"].sum(), before_lo=int((da["before"] <= 2).sum()),
                   worse_from=a_worse["before"].value_counts().to_dict(),
                   already4=int((da["before"] == 4).sum()), on3=int((da["before"] == 3).sum()))
    arcs_bus_street = a[flags["corr"] & a["edge"].map(e["name"]).isin(BUS_STREETS).values]
    off_road_m = arcs_bus_street.loc[~flags["bus"][arcs_bus_street.index], "len"].sum()
    a_stats["bus_street_arcs_off_road_m"] = off_road_m
    print("A:", a_stats)

    # Waterfront shared path near Customhouse / Jervois Quay.
    quay = e.loc[role.index][e.loc[role.index, "name"].isin(["Customhouse Quay", "Jervois Quay"])]
    zone = quay.geometry.union_all().buffer(120)
    cand = e[e["highway"].isin(["cycleway", "path", "footway"]) & e.geometry.intersects(zone)]
    cand = cand[cand["facility"].isin(["shared_path", "shared_footway", "track", "trail", "seg_path"])]
    wf = dict(edges=len(cand), length_m=cand["length_m"].sum(),
              lts_counts=pd.concat([cand["lts_fw"], cand["lts_bw"]]).value_counts().sort_index().to_dict())
    print("waterfront path:", wf)

    # GeoJSON
    g = e.loc[role.index, ["name", "highway", "length_m", "facility", "reserve_m", "can_fw", "can_bw", "lts_fw", "lts_bw",
                           "cross_u", "cross_v", "geometry"]].copy()
    g["role"] = role
    arc_idx = pd.MultiIndex.from_arrays([a["edge"], a["dir"]])
    for sc in ("A", "B", "C"):
        s = pd.Series(lts_sc[sc][0], index=arc_idx)
        for d, dn in ((0, "fw"), (1, "bw")):
            g[f"lts_{sc}_{dn}"] = s.xs(d, level=1).reindex(g.index)
    g["in_A"] = g["name"].isin(BUS_STREETS) & (g["role"] == "corridor")
    g["in_B"] = g["role"] == "corridor"
    g["in_C"] = True
    g["A_effect"] = "no change"
    worse = ((g["lts_A_fw"] > g["lts_fw"]) & g["can_fw"]) | ((g["lts_A_bw"] > g["lts_bw"]) & g["can_bw"])
    g.loc[worse, "A_effect"] = "worse"
    g["scenario_labels"] = np.where(g["in_A"], "A,B,C", np.where(g["in_B"], "B,C", "C"))
    for c in ("lts_A_fw", "lts_A_bw", "lts_B_fw", "lts_B_bw", "lts_C_fw", "lts_C_bw"):
        g[c] = g[c].where(g["can_fw" if c.endswith("fw") else "can_bw"])
    for c in ("lts_fw", "lts_bw"):
        g[c] = g[c].where(g["can_fw" if c.endswith("fw") else "can_bw"])
    g = g.rename(columns={"lts_fw": "lts_now_fw", "lts_bw": "lts_now_bw"})
    g["geometry"] = g.geometry.simplify(1)
    g.to_crs(4326).to_file(BLOCKS / "cycle_harbour_quays.geojson", driver="GeoJSON")

    write_md(out, streets, a_stats, wf, corr_km, conn_km, up, time.time() - t0)
    print(f"done in {time.time() - t0:.0f}s")


def write_md(out, streets, a_stats, wf, corr_km, conn_km, up, secs):
    def r(sc, lv):
        return out[(out["code"] == sc) & (out["level"] == lv)].iloc[0]

    lv_name = {"all_ages": "all ages (stress 1)", "confident": "confident riders (stress <= 2)"}
    L = ["# Harbour Quays bus priority: what it means for cycling", "",
         "Scenario analysis, Go Dutch trips (a mode-shift target) and census (today's riders), weekday "
         "outbound trips, connectivity cap 1.25. Method: `scripts/cycle_harbour_quays.py`.", ""]
    L += ["## Results", "",
          "| Scenario | Level | Go Dutch newly connected | Go Dutch % connected (today > after) | Census newly connected | "
          "Census % connected (today > after) | Lane cost, central (range) $M |",
          "|---|---|---|---|---|---|---|"]
    for sc in ("A", "B", "C"):
        for lv in LEVELS_USED:
            x = r(sc, lv)
            cost = f"{x['cost_central_M']:.1f} ({x['cost_low_M']:.1f}-{x['cost_high_M']:.1f})" if x["cost_central_M"] > 0 else "0 (in the $11M)"
            L.append(f"| {SCEN_LABEL[sc]} | {lv_name[lv]} | {x['newly_connected_godutch']:+,.0f} | "
                     f"{x['pct_today_godutch']:.1f}% > {x['pct_after_godutch']:.1f}% | "
                     f"{x['newly_connected_census']:+,.1f} | {x['pct_today_census']:.1f}% > {x['pct_after_census']:.1f}% | {cost} |")
    L.append("")
    xa1, xa2 = r("A", "all_ages"), r("A", "confident")
    L += ["## Scenario A: bus lanes as announced", "",
          f"Bike-usable peak bus lanes do not connect any new trips: {xa1['newly_connected_godutch']:+,.0f} Go Dutch trips at "
          f"all ages and {xa2['newly_connected_godutch']:+,.0f} for confident riders. Level 3 never counts as connected at "
          f"either standard, and 3 is the best a bus lane earns here.",
          f"Of {a_stats['arcs']} on-road directions on the four bus-lane streets, {a_stats['worse']} "
          f"({a_stats['worse_m']:,.0f} m) get worse: every one is already level 3 ({a_stats['on3']}) or level 4 "
          f"({a_stats['already4']}) today, so the max(today, 3) rule changes nothing. No direction rated 1 or 2 is on "
          f"an on-road lane there. A further {a_stats['bus_street_arcs_off_road_m']:,.0f} m of direction on these streets "
          f"is a sidepath or protected lane and is left alone. Nothing gets better in the model either: off peak the lane "
          f"is general traffic, so a level 4 direction is still level 4 outside the peaks.",
          f"Go Dutch trips lost: {abs(xa2.get('trips_lost_godutch', 0)):,.0f} (confident), "
          f"{abs(xa1.get('trips_lost_godutch', 0)):,.0f} (all ages).",
          "During the peak, a bike in a kerbside bus lane on a 50 km/h road is probably less stressful than sharing "
          "with two lanes of general traffic, but the model has no peak/off-peak split and buses at 30+ an hour are "
          "already rated 4. Read A as neutral for the model's standards, with a possible modest peak-hour gain "
          "for confident riders that this method cannot show.",
          f"The waterfront shared path beside Customhouse and Jervois Quay is not changed: {wf['edges']} edges, "
          f"{wf['length_m']:,.0f} m, stress counts by direction {wf['lts_counts']}. It stays the all-ages route "
          "along the harbour.", ""]
    xb1, xb2, xc1, xc2 = r("B", "all_ages"), r("B", "confident"), r("C", "all_ages"), r("C", "confident")
    L += ["## Scenarios B and C: adding protected lanes", "",
          f"B (protected lanes on {corr_km:.2f} street-km equivalent, signal priority at {up['B']} internal junctions) connects "
          f"{xb1['newly_connected_godutch']:+,.0f} Go Dutch trips at all ages ({xb1['pct_change_godutch']:+.2f} points) and "
          f"{xb2['newly_connected_godutch']:+,.0f} for confident riders ({xb2['pct_change_godutch']:+.2f} points). "
          f"Census trips: {xb1['newly_connected_census']:+,.1f} and {xb2['newly_connected_census']:+,.1f}.",
          f"C adds {conn_km:.2f} street-km equivalent of connectors and {up['C']} more junctions. Go Dutch: "
          f"{xc1['newly_connected_godutch']:+,.0f} at all ages ({xc1['pct_change_godutch']:+.2f} points), "
          f"{xc2['newly_connected_godutch']:+,.0f} for confident riders. The extra over B is "
          f"{xc1['newly_connected_godutch'] - xb1['newly_connected_godutch']:+,.0f} and "
          f"{xc2['newly_connected_godutch'] - xb2['newly_connected_godutch']:+,.0f}.",
          f"Lane cost, central $1.6M a km: B ${xb1['cost_central_M']:.1f}M (${xb1['cost_low_M']:.1f}-{xb1['cost_high_M']:.1f}M), "
          f"C ${xc1['cost_central_M']:.1f}M (${xc1['cost_low_M']:.1f}-{xc1['cost_high_M']:.1f}M). Junction priority is extra: "
          f"B {xb1['junctions_upgraded']} junctions, ${xb1['junction_cost_central_M']:.1f}M central "
          f"(${xb1['junction_cost_range_M']}M); C {xc1['junctions_upgraded']} junctions, ${xc1['junction_cost_central_M']:.1f}M "
          f"(${xc1['junction_cost_range_M']}M), an upper bound as some already have signals. "
          f"Go Dutch trips connected per $M of lanes at all ages: B {xb1['godutch_trips_per_M_central']:,.0f}, "
          f"C {xc1['godutch_trips_per_M_central']:,.0f}.", ""]
    L += ["## Where the gain goes (origin suburbs, Go Dutch trips newly connected)", ""]
    for sc in ("B", "C"):
        for lv in LEVELS_USED:
            L.append(f"- {SCEN_LABEL[sc]}, {lv_name[lv]}: {r(sc, lv)['top_suburbs_godutch']}.")
    L += ["", "## Why these connectors", "",
          "Bunny Street is the council's planned link from the Thorndon Quay cycleway to the waterfront, and it meets the "
          "corridor at the station end. The Featherston Street link runs alongside Whitmore Street to the same junction. "
          "Taranaki Street is the one route that leaves the corridor toward Courtenay Place and Te Aro. "
          "The southern end already meets the Newtown to City Cycleway and the Mt Victoria tunnel path at the Basin Reserve, "
          "so no connector was added there.", ""]
    L += ["## Road reserve width (feasibility)", "",
          "| Street | Role | Edges | Length m | Reserve median m | Reserve min m | Edges with a value |", "|---|---|---|---|---|---|---|"]
    fmt = lambda v: "" if pd.isna(v) else f"{v:.1f}"  # noqa: E731
    for _, s in streets.iterrows():
        L.append(f"| {s['name']} | {s['role']} | {s['edges']} | {s['length_m']:,.0f} | "
                 f"{fmt(s['reserve_median_m'])} | {fmt(s['reserve_min_m'])} | {s['reserve_n']} |")
    L += ["", "Rough arithmetic, not a design: two 3.2 m general lanes each way, two 2.5 m protected lanes with buffers and "
          "two 3 m footpaths need about 24 m, before any bus lane. Streets with a 20 m reserve (Cable Street, Wakefield "
          "Street) are tight; Cambridge and Kent Terrace (about 46 m) have room. The bus lanes are announced to be "
          "kerbside, so protected lanes would compete with them for the same width, or replace a general lane.", ""]
    L += ["## Caveats", "",
          "- The bus lanes are peak-only. Off peak they are general traffic, so they do not make the street safe for "
          "everyone. They suit confident riders at peak, if the buses are frequent and slow.",
          "- We cannot see the detailed designs. Scenario A is a stress rule (max of today and 3), not a design. "
          "Protected lanes are not announced; B and C show what they would add.",
          "- Sidepaths (shared footpaths) are rated stress 1 in the network model and left as they are. Much of Cable "
          "Street, Cambridge and Kent Terrace, and parts of Jervois Quay already have one, so B adds less there than the "
          "length suggests. The model does not know whether they are wide or busy enough.",
          "- The trip model has no peak/off-peak split and no bus-lane effect. Connected trips are a measure of "
          "network access, not a forecast of extra riders.",
          "- Costs are indicative, from per-km rates, not project estimates. Junction costs are an upper bound.",
          "- The Featherston Street and Taranaki Street connector limits are set by coordinates (north of the "
          "Whitmore/Customhouse junction; Wakefield Street to Courtenay Place). Check on a map.",
          f"- Runtime {secs:,.0f} s. Sources: metlink.org.nz project timeline for Harbour Quays; "
          "transportprojects.org.nz Harbour Quays bus priority (researched 30 Sep 2026)."]
    (TABLES / "cycle_harbour_quays.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
