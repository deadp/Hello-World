"""Where would Wellingtonians cycle with a good network? Propensity-to-cycle routing (PCT method).

1. Trips: 2023 Census main means of travel to work and to education, SA2 of residence -> SA2 of
   workplace / educational institution, for SA2s in the study area. Commuters exclude work or
   study at home. Suppressed cells (-999) count as 3 trips (totals) or 0 (a mode).
2. Where trips start and end within an SA2:
     homes: SA1 centroids weighted by 2023 census residents;
     workplaces: SA1 centroids weighted by estimated workers (Stats NZ 2024 employee counts
       spread over commercial floor area, fiscal_v2.py);
     education: schools, colleges and universities from OpenStreetMap;
   each snapped to the nearest node of the cycling network.
3. Routes: shortest path on the direction-aware network (cycle_network.py). Arc cost =
   metres x stress factor(level of traffic stress in that direction) + climb weight x metres
   climbed + a penalty for crossing a busy road at an unsignalised junction. The factors live in
   PARAMS; cycle_validate.py calibrates them against WCC's cyclist counters and writes
   outputs/tables/cycle_calibration.json, which this script uses when present.
   Outbound and return trips are routed separately (different hills, one-way streets and
   facilities each way).
4. Uptake: the Propensity to Cycle Tool's production commute model (npct/pct-scripts,
   uptake_pct_godutch_2020), with gradient centred at the Dutch average: g_c = g - 0.78, where g
   is the route's average gradient in % ((climb + descent) / length) and d its length in km
   (capped at 30):
     base  = -4.018 - 0.6369 d + 1.988 sqrt(d) + 0.008775 d^2 - 0.2555 g_c
             + 0.02006 d g_c - 0.1234 sqrt(d) g_c
     Go Dutch = base + 2.550 - 0.08036 d
     E-bike   = Go Dutch + 0.05509 d - 0.000295 d^2 + 0.1812 g_c
   Each scenario is floored at the people who already cycled on that route in 2023 (as in
   Scotland's Network Planning Tool), so potential never falls below today's cycling. The 2017
   model (uptake_pct_godutch, used in the first version) is kept for comparison (MODEL="2017").
5. Flows: each route's trips are added to every arc it uses, by direction. Scenarios: census 2023
   (people who cycled), Go Dutch, e-bike. Figures are trips per weekday.

Writes data/processed/cycle_flows.parquet (edge flows, total and by direction) and
outputs/tables/cycle_scenarios.csv.
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CYC = RAW / "cycling"
PROC = ROOT / "data" / "processed"
TABLES = ROOT / "outputs" / "tables"
PARAMS = dict(climb=10.0, stress={2: 1.0, 3: 1.1, 4: 1.25}, cross={3: 30.0, 4: 80.0})
MODEL = "2020"
CALIBRATION = TABLES / "cycle_calibration.json"
UPTAKE_2020 = dict(a=-4.018, d1=-0.6369, d2=1.988, d3=0.008775, h1=-0.2555, i1=0.02006, i2=-0.1234,
                   dutch_a=2.550, dutch_d1=-0.08036, eb_d1=0.05509, eb_d3=-0.000295, eb_h1=0.1812, g0=0.78)
GODUTCH_2017 = dict(a=-3.959 + 2.523, d1=-0.5963 - 0.07626, d2=1.866, d3=0.00805, h1=-0.2710, i1=0.009394,
                    i2=-0.05135)
PURPOSE = {
    "work": ("census_work_od.csv", "workplace_address", "2023_Work_at_home"),
    "education": ("census_edu_od.csv", "educational_institution_address", "2023_Study_at_home"),
}


def uptake(d_km, grad, ebike=False, model=None):
    d = np.minimum(d_km, 30)
    if (model or MODEL) == "2017":
        c = dict(GODUTCH_2017)
        g = grad
        if ebike:
            c["d1"] += 0.05509
            c["d3"] += -0.000295
            c["h1"] += 0.1812
        logit = (c["a"] + c["d1"] * d + c["d2"] * np.sqrt(d) + c["d3"] * d ** 2 + c["h1"] * g
                 + c["i1"] * d * g + c["i2"] * np.sqrt(d) * g)
    else:
        c = UPTAKE_2020
        g = grad - c["g0"]
        logit = (c["a"] + c["d1"] * d + c["d2"] * np.sqrt(d) + c["d3"] * d ** 2 + c["h1"] * g
                 + c["i1"] * d * g + c["i2"] * np.sqrt(d) * g + c["dutch_a"] + c["dutch_d1"] * d)
        if ebike:
            logit = logit + c["eb_d1"] * d + c["eb_d3"] * d ** 2 + c["eb_h1"] * g
    return 1 / (1 + np.exp(-logit))


def load_params():
    if CALIBRATION.exists():
        p = json.loads(CALIBRATION.read_text())["best"]
        return dict(climb=p["climb"], stress={int(k): v for k, v in p["stress"].items()},
                    cross={int(k): v for k, v in p["cross"].items()})
    return PARAMS


def graph(params=None, verbose=True):
    p = params or PARAMS
    e = gpd.read_parquet(PROC / "cycle_edges.parquet")
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    fw = pd.DataFrame({"u": e["u"], "v": e["v"], "edge": e.index, "dir": 0, "len": e["length_m"],
                       "up": e["up_fw"], "rise": e["rise_abs"], "lts": e["lts_fw"], "cross": e["cross_v"]})[
        e["can_fw"].values]
    bw = pd.DataFrame({"u": e["v"], "v": e["u"], "edge": e.index, "dir": 1, "len": e["length_m"],
                       "up": e["up_bw"], "rise": e["rise_abs"], "lts": e["lts_bw"], "cross": e["cross_u"]})[
        e["can_bw"].values]
    arcs = pd.concat([fw, bw], ignore_index=True)
    stress = arcs["lts"].map(p["stress"]).fillna(1.0).values
    cross = arcs["cross"].map(p["cross"]).fillna(0.0).values
    arcs["cost"] = arcs["len"] * stress + p["climb"] * arcs["up"] + cross
    arcs = arcs.sort_values("cost").drop_duplicates(["u", "v"]).reset_index(drop=True)
    n = len(nodes)
    G = csr_matrix((arcs["cost"].values + 1e-6, (arcs["u"].values, arcs["v"].values)), shape=(n, n))
    ncomp, lab = connected_components(G, directed=True, connection="strong")
    main = np.bincount(lab).argmax()
    keep = lab == main
    if verbose:
        print(f"network: {n:,} nodes, largest strongly connected component {keep.sum():,}")
    # Arc lookup (u, v) -> arc row, for turning predecessor trees into edge flows.
    A = csr_matrix((np.arange(len(arcs)) + 1, (arcs["u"].values, arcs["v"].values)), shape=(n, n))
    return e, nodes, arcs, G, A, keep


def snap(points_xy, nodes, keep):
    idx = np.where(keep)[0]
    tree = cKDTree(nodes[["x", "y"]].values[idx])
    d, i = tree.query(points_xy)
    return idx[i], d


def zones():
    """SA1 points with residents, workers and SA2 (2023 codes)."""
    sa1 = gpd.read_file(RAW / "sa1.geojson").to_crs(2193)
    census = gpd.read_file(RAW / "sa1_census2023.geojson", ignore_geometry=True)
    pop = pd.to_numeric(census.set_index("SA12023_V1_00")["VAR_1_3"], errors="coerce").clip(lower=0)
    pts = gpd.GeoDataFrame({"sa1": sa1["SA12025_V1_00"]}, geometry=sa1.geometry.representative_point(), crs=2193)
    sa2 = gpd.read_parquet(RAW / "networks" / "employees_sa2.parquet").to_crs(2193)
    pts = gpd.sjoin(pts, sa2[["SA22023_V1_00", "geometry"]], predicate="within").rename(
        columns={"SA22023_V1_00": "sa2"}).drop(columns="index_right")
    pts["residents"] = pts["sa1"].map(pop).fillna(0)
    # Workers by SA1 from the v2 cost model's unit-level estimates.
    units = gpd.read_parquet(PROC / "units.parquet")[["ValuationID", "geometry"]]
    w = pd.read_parquet(PROC / "fiscal_units_v2.parquet", columns=["workers"])
    units = units.merge(w, left_on="ValuationID", right_index=True)
    up = gpd.GeoDataFrame(units[["workers"]], geometry=units.geometry.representative_point(), crs=2193)
    ws = gpd.sjoin(up, sa1[["SA12025_V1_00", "geometry"]], predicate="within").groupby("SA12025_V1_00")["workers"].sum()
    pts["workers"] = pts["sa1"].map(ws).fillna(0)
    edu = json.loads((CYC / "osm_education.json").read_text())["elements"]
    ep = [(e.get("lon") or e["center"]["lon"], e.get("lat") or e["center"]["lat"], e["tags"].get("name"))
          for e in edu if "lon" in e or "center" in e]
    ep = gpd.GeoDataFrame({"name": [n for _, _, n in ep]}, geometry=gpd.points_from_xy(
        [a for a, _, _ in ep], [b for _, b, _ in ep]), crs=4326).to_crs(2193)
    ep = gpd.sjoin(ep, sa2[["SA22023_V1_00", "geometry"]], predicate="within").rename(
        columns={"SA22023_V1_00": "sa2"})
    return pts, ep, sa2


def endpoints(pts, ep, sa2, nodes, keep):
    """Per SA2: origin nodes with weights (homes), and destination nodes for work and education."""
    pts = pts.copy()
    pts["node"], pts["snap_m"] = snap(np.column_stack([pts.geometry.x, pts.geometry.y]), nodes, keep)
    rep = sa2.set_index("SA22023_V1_00").geometry.representative_point()
    rep_node, rep_d = snap(np.column_stack([rep.x, rep.y]), nodes, keep)
    rep_node = pd.Series(rep_node, index=rep.index)

    def table(df, col):
        t = df[df[col] > 0].groupby(["sa2", "node"])[col].sum()
        return t / t.groupby(level=0).transform("sum")

    home = table(pts, "residents")
    work = table(pts, "workers")
    ep = ep.assign(node=snap(np.column_stack([ep.geometry.x, ep.geometry.y]), nodes, keep)[0], w=1.0)
    edu = table(ep, "w")
    # Fallbacks: SA2s without SA1-level data (outside the city) use population, then the SA2 point.
    for s, n in rep_node.items():
        if s not in home.index.get_level_values(0):
            home.loc[(s, n)] = 1.0
        if s not in work.index.get_level_values(0):
            if s in home.index.get_level_values(0):
                for (s2, n2), v in home.loc[[s]].items():
                    work.loc[(s2, n2)] = v
            else:
                work.loc[(s, n)] = 1.0
        if s not in edu.index.get_level_values(0):
            for (s2, n2), v in work.loc[[s]].items():
                edu.loc[(s2, n2)] = v
    return home.sort_index(), {"work": work.sort_index(), "education": edu.sort_index()}, set(rep.index)


def od(purpose, sa2s):
    f, dest_col, home_col = PURPOSE[purpose]
    d = pd.read_csv(CYC / f, encoding="utf-8-sig", dtype={"SA22023_V1_00_usual_residence_address": str,
                                                          f"SA22023_V1_00_{dest_col}": str})
    d = d.rename(columns={"SA22023_V1_00_usual_residence_address": "o", f"SA22023_V1_00_{dest_col}": "d"})
    d = d[d["o"].isin(sa2s) & d["d"].isin(sa2s)].copy()
    total = d["2023_Total_stated"].where(d["2023_Total_stated"] >= 0, 3)
    at_home = d[home_col].clip(lower=0)
    d["trips"] = (total - at_home).clip(lower=0)
    d["bike"] = d["2023_Bicycle"].clip(lower=0)
    return d[["o", "d", "trips", "bike"]]


def tree_paths(pred, src, arcs_len, arcs_rise, A):
    """For a shortest-path tree: arc into each node, route length, rise and depth from the source."""
    n = len(pred)
    has = pred >= 0
    arc = np.full(n, -1)
    arc[has] = np.asarray(A[pred[has], np.where(has)[0]]).ravel() - 1
    V = np.zeros((n, 3))
    V[has, 0], V[has, 1], V[has, 2] = arcs_len[arc[has]], arcs_rise[arc[has]], 1
    p = np.where(has, pred, np.arange(n))
    p[src] = src
    # Pointer jumping (Wyllie): after k rounds each node holds the sum over its first 2^k arcs
    # towards the root; the root and unreached nodes point to themselves and hold zeros.
    while not (p == p[p]).all():
        V = V + V[p]
        p = p[p]
    return arc, V[:, 0], V[:, 1], V[:, 2].astype(int)


def accumulate(pred, depth, demand):
    """Push node demands (n x k) up the tree, deepest level first; returns subtree totals."""
    acc = demand.copy()
    live = np.where(demand.sum(axis=1) > 0)[0]
    if len(live) == 0:
        return acc
    order = np.argsort(-depth)
    order = order[depth[order] > 0]
    levels = np.split(order, np.flatnonzero(np.diff(depth[order])) + 1)
    top = depth[live].max()
    for lvl in levels:
        if depth[lvl[0]] > top:
            continue
        np.add.at(acc, pred[lvl], acc[lvl])
    return acc


def run(params=None, scenarios=("census", "godutch", "ebike"), floor=True, model=None, verbose=True,
        setup=None):
    """Route all trips; return (edge flows by scenario, arc flows, route summary rows, arcs)."""
    e, nodes, arcs, G, A, keep = graph(params, verbose)
    if setup is None:
        pts, ep, sa2 = zones()
        setup = endpoints(pts, ep, sa2, nodes, keep)
    home, dests, sa2s = setup
    alen, arise = arcs["len"].values, arcs["rise"].values
    k_s = len(scenarios)
    arc_flow = np.zeros((len(arcs), k_s))
    route_rows = []

    def as_arrays(table):
        return {k: (g.index.get_level_values(1).values, g.values / g.values.sum())
                for k, g in table.groupby(level=0)}

    for purpose, dest in dests.items():
        trips = od(purpose, sa2s)
        if verbose:
            print(f"{purpose}: {len(trips):,} SA2 pairs, {trips['trips'].sum():,.0f} commuters, "
                  f"{trips['bike'].sum():,.0f} cycled")
        for direction in ("out", "back"):
            src_table, snk_table = (home, dest) if direction == "out" else (dest, home)
            key_src, key_snk = ("o", "d") if direction == "out" else ("d", "o")
            snk = as_arrays(snk_table)
            by_src = {k: (g[key_snk].values, g["trips"].values, g["bike"].values)
                      for k, g in trips.groupby(key_src)}
            src_nodes = src_table.index.get_level_values(1).unique()
            dist, pred = dijkstra(G, directed=True, indices=src_nodes.values, return_predecessors=True)
            for k, sn in enumerate(src_nodes):
                rows = src_table.xs(sn, level=1)
                dem = np.zeros((len(nodes), k_s))
                arc, L, R, depth = tree_paths(pred[k], sn, alen, arise, A)
                for s2, wsrc in rows.items():
                    if s2 not in by_src:
                        continue
                    for other, n_od, b_od in zip(*by_src[s2]):
                        if other not in snk:
                            continue
                        tn, wt = snk[other]
                        ok = np.isfinite(dist[k, tn])
                        if not ok.any():
                            continue
                        tn, wt = tn[ok], wt[ok] / wt[ok].sum()
                        d_km = L[tn] / 1000
                        grad = np.where(L[tn] > 0, R[tn] / np.maximum(L[tn], 1) * 100, 0)
                        n_trips = n_od * wsrc * wt
                        bike = b_od * wsrc * wt
                        cols = []
                        for sc in scenarios:
                            if sc == "census":
                                cols.append(bike)
                            else:
                                v = n_trips * uptake(d_km, grad, ebike=sc == "ebike", model=model)
                                cols.append(np.maximum(v, bike) if floor else v)
                        np.add.at(dem, tn, np.column_stack(cols))
                        if direction == "out":
                            route_rows.append((purpose, n_trips.sum(), (n_trips * d_km).sum(),
                                               (n_trips * grad).sum(), *[c.sum() for c in cols]))
                if dem.sum() == 0:
                    continue
                acc = accumulate(pred[k], depth, dem)
                has = arc >= 0
                np.add.at(arc_flow, arc[has], acc[has])
            if verbose:
                print(f"  {direction}: routed from {len(src_nodes):,} origins")
    flow = np.zeros((len(e), k_s))
    np.add.at(flow, arcs["edge"].values, arc_flow)
    return flow, arc_flow, route_rows, arcs, e, setup


def main():
    params = load_params()
    print("route choice parameters:", params, "(calibrated)" if CALIBRATION.exists() else "(default)")
    scen = ["census", "godutch", "ebike"]
    flow, arc_flow, route_rows, arcs, e, _ = run(params, scen)
    r = pd.DataFrame(route_rows, columns=["purpose", "trips", "km", "grad"] + scen).groupby("purpose").sum()
    r["mean_km"] = r["km"] / r["trips"]
    r["mean_grad_%"] = r["grad"] / r["trips"]
    for s in scen:
        r[f"{s}_share_%"] = r[s] / r["trips"] * 100
    r.round(2).to_csv(TABLES / "cycle_scenarios.csv")
    print(r.round(2).to_string())
    out = pd.DataFrame(flow, columns=[f"flow_{s}" for s in scen], index=e.index)
    for d, name in ((0, "fw"), (1, "bw")):
        m = arcs["dir"].values == d
        part = np.zeros((len(e), len(scen)))
        np.add.at(part, arcs["edge"].values[m], arc_flow[m])
        for i, s in enumerate(scen):
            out[f"flow_{s}_{name}"] = part[:, i]
    out.to_parquet(PROC / "cycle_flows.parquet")
    print(out[[f"flow_{s}" for s in scen]].describe().round(0).to_string())


if __name__ == "__main__":
    main()
