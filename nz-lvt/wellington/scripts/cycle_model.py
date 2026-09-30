"""Where would Wellingtonians cycle with a good network? Propensity-to-cycle routing (PCT method).

1. Trips: 2023 Census main means of travel to work and to education, SA2 of residence -> SA2 of
   workplace / educational institution, for SA2s in the study area. Commuters exclude work or
   study at home. Suppressed cells (-999) count as 3 trips (totals) or 0 (a mode).
   Education trips are split by what is in the destination SA2 (split_education): school trips
   = alpha x school roll there (alpha = education trips per enrolled pupil, estimated in SA2s with
   no tertiary campus), split primary vs secondary by roll (contributing and full primary ->
   primary; intermediate and secondary -> secondary; composite half each); the rest are tertiary.
2. Where trips start and end within an SA2:
     homes: property points (rating units) weighted by residents, scaled to each SA1's 2023
     census count (SA1 centroids outside the city);
     workplaces: property points weighted by estimated workers (Stats NZ 2024 employee counts
       spread over commercial floor area, fiscal_v2.py);
     schools: Ministry of Education directory, weighted by roll; tertiary: OSM universities and
       colleges (other OSM education points where an SA2 has none);
   each snapped to the nearest node of the cycling network.
3. Routes: shortest path on the direction-aware network (cycle_network.py). Arc cost =
   metres x stress factor(level of traffic stress in that direction; x a "roadside" factor on
   shared paths beside fast or busy roads) + climb weight x metres climbed + a penalty for crossing a busy road at an unsignalised junction. The factors live in
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
   School trips use the PCT schools models (Goodman et al. 2019; npct/pct-scripts
   05.2_school_scenario_building.do), g_c = g - 0.63:
     primary   base = -4.813 + 0.9743 d - 0.2401 d^2 - 0.4245 g_c;  Go Dutch = base + 3.642
               for trips <= 5 km (no change beyond)
     secondary base = -7.178 - 1.870 d + 5.961 sqrt(d) - 0.5290 g_c;  Go Dutch = base + 3.574
               + 0.3438 d for trips <= 10 km
   (the e-bike scenario leaves school trips at Go Dutch; tertiary trips use the commute model).
   Each scenario is floored at the people who already cycled on that route in 2023 (as in
   Scotland's Network Planning Tool), so potential never falls below today's cycling. The 2017
   model (uptake_pct_godutch, used in the first version) is kept for comparison (MODEL="2017").
5. Non-commute trips (run_utility): shopping, visiting and leisure trips from homes, following
   Scotland's Network Planning Tool: outbound trips per resident relative to work trips
   (shopping 1.08x, visiting 0.50x, leisure 0.27x), a gravity model to OSM shops, cafes, parks,
   beaches, sports and attractions and to other homes, 0.5-15 km, with the distance decay fitted
   so mean trip lengths are 0.45 / 0.76 / 0.7 of the commute; cycling today = the origin SA2's
   commute cycling share (x 0.5 for shopping); scenario uptake from the same models (x 0.5 for
   shopping), floored at today. Return trips follow the outbound route in reverse.
6. Flows: each route's trips are added to every arc it uses, by direction. Scenarios: census 2023
   (people who cycled), Go Dutch, e-bike, and "local": the Wellington-fitted commute model
   (cycle_uptake_fit.py) with every route made low-stress, for work and tertiary trips (school
   trips stay at today's cycling). Figures are trips per weekday.

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
PARAMS = dict(climb=10.0, stress={2: 1.0, 3: 1.1, 4: 1.25}, cross={3: 30.0, 4: 80.0}, roadside=1.0)
MODEL = "2020"
TRAIL_FACTOR = 1.5  # an unpaved trail metre costs 1.5 paved metres (assumed, not calibrated)
CALIBRATION = TABLES / "cycle_calibration.json"
UPTAKE_2020 = dict(a=-4.018, d1=-0.6369, d2=1.988, d3=0.008775, h1=-0.2555, i1=0.02006, i2=-0.1234,
                   dutch_a=2.550, dutch_d1=-0.08036, eb_d1=0.05509, eb_d3=-0.000295, eb_h1=0.1812, g0=0.78)
GODUTCH_2017 = dict(a=-3.959 + 2.523, d1=-0.5963 - 0.07626, d2=1.866, d3=0.00805, h1=-0.2710, i1=0.009394,
                    i2=-0.05135)
SCHOOL = dict(primary=dict(a=-4.813, d1=0.9743, d3=-0.2401, h1=-0.4245, dutch=3.642, dutch_d1=0.0, dmax=5.0),
              secondary=dict(a=-7.178, d1=-1.870, d2=5.961, h1=-0.5290, dutch=3.574, dutch_d1=0.3438, dmax=10.0),
              g0=0.63)
SCHOOL_STREAM = {"Contributing": {"primary": 1.0}, "Full Primary": {"primary": 1.0},
                 "Intermediate": {"secondary": 1.0}, "Secondary (Year 9-15)": {"secondary": 1.0},
                 "Secondary (Year 7-15)": {"secondary": 1.0}, "Secondary (Year 7-10)": {"secondary": 1.0},
                 "Composite": {"primary": 0.5, "secondary": 0.5}}
STREAM_KIND = {"work": "commute", "tertiary": "commute", "primary": "primary", "secondary": "secondary"}
PROTECTED = {"track", "protected_lane", "sidepath", "seg_path", "shared_path", "shared_footway"}
PURPOSE = {
    "work": ("census_work_od.csv", "workplace_address", "2023_Work_at_home"),
    "education": ("census_edu_od.csv", "educational_institution_address", "2023_Study_at_home"),
}


def uptake(d_km, grad, ebike=False, model=None, kind="commute"):
    if kind in ("primary", "secondary"):
        c = SCHOOL[kind]
        d = np.asarray(d_km, dtype=float)
        g = grad - SCHOOL["g0"]
        base = c["a"] + c["d1"] * d + c.get("d2", 0) * np.sqrt(d) + c.get("d3", 0) * d ** 2 + c["h1"] * g
        logit = np.where(d <= c["dmax"], base + c["dutch"] + c["dutch_d1"] * d, base)
        return 1 / (1 + np.exp(-logit))
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


LOCAL = TABLES / "cycle_uptake_local.json"
# Non-commute trips (Scotland's Network Planning Tool method). Outbound trips per weekday relative
# to work trips (Scottish Household Survey purpose shares excluding "go home": commuting 23.3%,
# shopping 25.1%, social/visiting 11.7%, leisure 6.3%); mean trip length relative to the commute
# (England NTS: shopping 0.45, visiting 0.76, leisure 0.7); cycling share today = the origin's
# commute cycling share x 0.5 for shopping (NPT); uptake models x 0.5 for shopping.
UTILITY = dict(shopping=dict(rate=25.1 / 23.3, length=0.45, factor=0.5),
               visiting=dict(rate=11.7 / 23.3, length=0.76, factor=1.0),
               leisure=dict(rate=6.3 / 23.3, length=0.70, factor=1.0))
SHOP_WEIGHT = {"supermarket": 15, "mall": 25, "department_store": 15, "convenience": 1}
LEISURE_WEIGHT = {"stadium": 5, "swimming_pool": 5, "sports_centre": 3, "beach": 4, "museum": 5, "zoo": 5,
                  "attraction": 3, "park": 1, "marina": 2, "cinema": 3, "theatre": 3, "library": 3}


def uptake_local(d_km, grad, busy, prot):
    """Wellington-fitted commute model (cycle_uptake_fit.py) with every route made low-stress."""
    from cycle_uptake_fit import predict
    c = json.loads(LOCAL.read_text())["coef"]
    return predict(c, d_km, grad, 0.0, np.minimum(prot + busy, 1.0))


def load_params():
    if CALIBRATION.exists():
        p = json.loads(CALIBRATION.read_text())["best"]
        return dict(climb=p["climb"], stress={int(k): v for k, v in p["stress"].items()},
                    cross={int(k): v for k, v in p["cross"].items()}, roadside=p.get("roadside", 1.0))
    return PARAMS


def graph(params=None, verbose=True):
    p = params or PARAMS
    e = gpd.read_parquet(PROC / "cycle_edges.parquet")
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    fw = pd.DataFrame({"u": e["u"], "v": e["v"], "edge": e.index, "dir": 0, "len": e["length_m"],
                       "up": e["up_fw"], "rise": e["rise_abs"], "lts": e["lts_fw"], "cross": e["cross_v"],
                       "fac": e["fac_fw"], "roadside": e["roadside_fw"]})[
        e["can_fw"].values]
    bw = pd.DataFrame({"u": e["v"], "v": e["u"], "edge": e.index, "dir": 1, "len": e["length_m"],
                       "up": e["up_bw"], "rise": e["rise_abs"], "lts": e["lts_bw"], "cross": e["cross_u"],
                       "fac": e["fac_bw"], "roadside": e["roadside_bw"]})[
        e["can_bw"].values]
    arcs = pd.concat([fw, bw], ignore_index=True)
    arcs["hs_len"] = arcs["len"] * (arcs["lts"] >= 3)
    arcs["prot_len"] = arcs["len"] * arcs["fac"].isin(PROTECTED)
    stress = arcs["lts"].map(p["stress"]).fillna(1.0).values
    cross = arcs["cross"].map(p["cross"]).fillna(0.0).values
    stress = stress * np.where(arcs["roadside"], p.get("roadside", 1.0), 1.0)
    # Unpaved trails (Town Belt tracks, MTB trails): rough and often steep, so slower going.
    stress = stress * np.where(arcs["fac"] == "trail", p.get("trail", TRAIL_FACTOR), 1.0)
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
    """SA1 points with residents, workers and SA2 (2023 codes); education points; schools."""
    sa1 = gpd.read_file(RAW / "sa1.geojson").to_crs(2193)
    census = gpd.read_file(RAW / "sa1_census2023.geojson", ignore_geometry=True)
    pop = pd.to_numeric(census.set_index("SA12023_V1_00")["VAR_1_3"], errors="coerce").clip(lower=0)
    sa2 = gpd.read_parquet(RAW / "networks" / "employees_sa2.parquet").to_crs(2193)
    # Homes and workplaces at property level (the v2 cost model's residents and workers per rating
    # unit), so an SA1's trips start from all its streets, not one centroid snapped to one node.
    # Residents are scaled to each SA1's census count. Units are pooled in 20 m cells per SA1.
    units = gpd.read_parquet(PROC / "units.parquet")[["ValuationID", "geometry"]]
    fu = pd.read_parquet(PROC / "fiscal_units_v2.parquet", columns=["residents", "workers"])
    units = units.merge(fu, left_on="ValuationID", right_index=True)
    up = gpd.GeoDataFrame(units[["residents", "workers"]], geometry=units.geometry.representative_point(), crs=2193)
    up = gpd.sjoin(up, sa1[["SA12025_V1_00", "geometry"]], predicate="within").rename(
        columns={"SA12025_V1_00": "sa1"}).drop(columns="index_right")
    up = up[(up["residents"] > 0) | (up["workers"] > 0)]
    up["cx"], up["cy"] = (up.geometry.x // 20).astype(int), (up.geometry.y // 20).astype(int)
    up["x"], up["y"] = up.geometry.x, up.geometry.y
    cells = up.groupby(["sa1", "cx", "cy"]).agg(x=("x", "mean"), y=("y", "mean"), residents=("residents", "sum"),
                                                 workers=("workers", "sum")).reset_index()
    tot = cells.groupby("sa1")["residents"].transform("sum")
    census_pop = cells["sa1"].map(pop)
    cells["residents"] = np.where((tot > 0) & census_pop.notna(), cells["residents"] / tot.where(tot > 0, 1) *
                                  census_pop.fillna(0), cells["residents"])
    # SA1s with no rated units (outside the city): one centroid with census residents.
    rest = sa1[~sa1["SA12025_V1_00"].isin(cells["sa1"])]
    rp = rest.geometry.representative_point()
    extra = pd.DataFrame({"sa1": rest["SA12025_V1_00"].values, "x": rp.x.values, "y": rp.y.values,
                          "residents": rest["SA12025_V1_00"].map(pop).fillna(0).values, "workers": 0.0})
    cells = pd.concat([cells.drop(columns=["cx", "cy"]), extra], ignore_index=True)
    pts = gpd.GeoDataFrame(cells[["sa1", "residents", "workers"]], geometry=gpd.points_from_xy(cells["x"], cells["y"]),
                           crs=2193)
    pts = gpd.sjoin(pts, sa2[["SA22023_V1_00", "geometry"]], predicate="within").rename(
        columns={"SA22023_V1_00": "sa2"}).drop(columns="index_right")
    edu = json.loads((CYC / "osm_education.json").read_text())["elements"]
    ep = [(e.get("lon") or e["center"]["lon"], e.get("lat") or e["center"]["lat"], e["tags"].get("name"),
           e["tags"].get("amenity")) for e in edu if "lon" in e or "center" in e]
    ep = gpd.GeoDataFrame({"name": [r[2] for r in ep], "amenity": [r[3] for r in ep]}, geometry=gpd.points_from_xy(
        [r[0] for r in ep], [r[1] for r in ep]), crs=4326).to_crs(2193)
    ep = gpd.sjoin(ep, sa2[["SA22023_V1_00", "geometry"]], predicate="within").rename(
        columns={"SA22023_V1_00": "sa2"}).drop(columns="index_right")
    sch = gpd.read_file(CYC / "schools.geojson").set_crs(2193, allow_override=True)  # queried with outSR=2193
    sch = sch[(sch["Status"] == "Open") & (pd.to_numeric(sch["Total"], errors="coerce") > 0)]
    rows = []
    for _, r in sch.iterrows():
        for stream, share in SCHOOL_STREAM.get(r["Org_Type"], {}).items():
            rows.append(dict(name=r["Org_Name"], stream=stream, roll=float(r["Total"]) * share, geometry=r.geometry))
    sch = gpd.GeoDataFrame(rows, geometry="geometry", crs=2193)
    sch = gpd.sjoin(sch, sa2[["SA22023_V1_00", "geometry"]], predicate="within").rename(
        columns={"SA22023_V1_00": "sa2"}).drop(columns="index_right")
    return pts, ep, sa2, sch


def endpoints(pts, ep, sa2, nodes, keep, sch=None):
    """Per SA2: origin nodes with weights (homes), and destination nodes per trip stream."""
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
    tert = table(ep[ep["amenity"].isin(["university", "college"])], "w")
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
        if s not in tert.index.get_level_values(0):
            for (s2, n2), v in edu.loc[[s]].items():
                tert.loc[(s2, n2)] = v
    dests = {"work": work.sort_index(), "tertiary": tert.sort_index()}
    roll = pd.DataFrame()
    if sch is not None:
        sch = sch.assign(node=snap(np.column_stack([sch.geometry.x, sch.geometry.y]), nodes, keep)[0])
        for st in ("primary", "secondary"):
            dests[st] = table(sch[sch["stream"] == st], "roll").sort_index()
        roll = sch.pivot_table(index="sa2", columns="stream", values="roll", aggfunc="sum").fillna(0)
    uni_sa2 = set(ep.loc[ep["amenity"].isin(["university", "college"]), "sa2"])
    return home.sort_index(), dests, set(rep.index), roll, uni_sa2


def od(purpose, sa2s):
    f, dest_col, home_col = PURPOSE[purpose]
    d = pd.read_csv(CYC / f, encoding="utf-8-sig", dtype={"SA22023_V1_00_usual_residence_address": str,
                                                          f"SA22023_V1_00_{dest_col}": str})
    d = d.rename(columns={"SA22023_V1_00_usual_residence_address": "o", f"SA22023_V1_00_{dest_col}": "d"})
    d = d[d["o"].isin(sa2s) & d["d"].isin(sa2s)].copy()
    for yr, sfx in (("2023", ""), ("2018", "18")):
        total = d[f"{yr}_Total_stated"].where(d[f"{yr}_Total_stated"] >= 0, 3)
        at_home = d[home_col.replace("2023", yr)].clip(lower=0)
        d["trips" + sfx] = (total - at_home).clip(lower=0)
        d["bike" + sfx] = d[f"{yr}_Bicycle"].clip(lower=0)
        d["suppressed" + sfx] = d[f"{yr}_Total_stated"] < 0
    return d[["o", "d", "trips", "bike", "suppressed", "trips18", "bike18", "suppressed18"]]


def split_education(trips, roll, uni_sa2):
    """Split SA2 education trips into primary, secondary and tertiary streams by destination."""
    into = trips.groupby("d")["trips"].sum()
    r = roll.reindex(into.index).fillna(0)
    school = r.sum(axis=1)
    no_uni = [s for s in into.index if s not in uni_sa2 and school.get(s, 0) > 0]
    alpha = min(1.0, into[no_uni].sum() / school[no_uni].sum()) if no_uni else 0.8
    sch_trips = np.minimum(into, alpha * school)
    share = pd.DataFrame({"primary": sch_trips * r.get("primary", 0) / school.replace(0, np.nan),
                          "secondary": sch_trips * r.get("secondary", 0) / school.replace(0, np.nan)}).fillna(0)
    share = share.div(into.replace(0, np.nan), axis=0).fillna(0)
    share["tertiary"] = (1 - share.sum(axis=1)).clip(lower=0)
    out = {}
    for st in ("primary", "secondary", "tertiary"):
        f = trips["d"].map(share[st]).fillna(1.0 if st == "tertiary" else 0.0)
        t = trips.copy()
        for c in ("trips", "bike", "trips18", "bike18"):
            t[c] = t[c] * f
        out[st] = t[t["trips"] > 0]
    return out, alpha, share


def tree_paths(pred, src, attrs, A):
    """For a shortest-path tree: arc into each node, sums of arc attributes (n x k) from the source,
    and depth."""
    n = len(pred)
    has = pred >= 0
    arc = np.full(n, -1)
    arc[has] = np.asarray(A[pred[has], np.where(has)[0]]).ravel() - 1
    k = attrs.shape[1]
    V = np.zeros((n, k + 1))
    V[has, :k] = attrs[arc[has]]
    V[has, k] = 1
    p = np.where(has, pred, np.arange(n))
    p[src] = src
    # Pointer jumping (Wyllie): after k rounds each node holds the sum over its first 2^k arcs
    # towards the root; the root and unreached nodes point to themselves and hold zeros.
    while not (p == p[p]).all():
        V = V + V[p]
        p = p[p]
    return arc, V[:, :k], V[:, k].astype(int)


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


def utility_destinations(pts, nodes, keep):
    """Attraction per network node for shopping, leisure and visiting."""
    els = json.loads((CYC / "osm_destinations.json").read_text())["elements"]
    rows = []
    for e in els:
        t = e.get("tags", {})
        lon, lat = (e["lon"], e["lat"]) if "lon" in e else (e["center"]["lon"], e["center"]["lat"])
        if "shop" in t:
            rows.append(("shopping", lon, lat, SHOP_WEIGHT.get(t["shop"], 1)))
        elif t.get("amenity") in ("cafe", "restaurant", "pub", "bar", "marketplace"):
            rows.append(("shopping", lon, lat, 0.5))
        else:
            k = t.get("leisure") or t.get("tourism") or t.get("amenity") or t.get("natural")
            if k in ("playground", "pitch", "garden", "fitness_centre", "viewpoint", "gallery", "community_centre",
                     "place_of_worship", "park", "stadium", "swimming_pool", "sports_centre", "beach", "museum",
                     "zoo", "attraction", "marina", "cinema", "theatre", "library"):
                rows.append(("leisure", lon, lat, LEISURE_WEIGHT.get(k, 1)))
    d = gpd.GeoDataFrame(pd.DataFrame(rows, columns=["purpose", "lon", "lat", "w"]),
                         geometry=gpd.points_from_xy([r[1] for r in rows], [r[2] for r in rows]), crs=4326).to_crs(2193)
    d["node"] = snap(np.column_stack([d.geometry.x, d.geometry.y]), nodes, keep)[0]
    out = {p: d[d["purpose"] == p].groupby("node")["w"].sum() for p in ("shopping", "leisure")}
    out["visiting"] = pts[pts["residents"] > 0].groupby("node")["residents"].sum()
    return out


def streams(setup):
    """Trip streams: (name, SA2-pair trips, destination table)."""
    home, dests, sa2s, roll, uni_sa2 = setup
    out = [("work", od("work", sa2s), dests["work"])]
    edu = od("education", sa2s)
    if "primary" in dests:
        parts, alpha, _ = split_education(edu, roll, uni_sa2)
        for st in ("primary", "secondary", "tertiary"):
            out.append((st, parts[st], dests[st]))
    else:
        out.append(("tertiary", edu, dests["tertiary"]))
    return out


LAST_MATRIX = {}


def run(params=None, scenarios=("census", "godutch", "ebike"), floor=True, model=None, verbose=True,
        setup=None, collect_od=False, only=None, utility=True, collect_matrix=False):
    """Route all trips; return (edge flows by scenario, arc flows, route summary rows, arcs, edges,
    setup[, per-SA2-pair route metrics])."""
    e, nodes, arcs, G, A, keep = graph(params, verbose)
    if setup is None:
        pts, ep, sa2, sch = zones()
        setup = endpoints(pts, ep, sa2, nodes, keep, sch)
    home = setup[0]
    attrs = arcs[["len", "rise", "hs_len", "prot_len"]].values
    k_s = len(scenarios)
    arc_flow = np.zeros((len(arcs), k_s))
    route_rows, od_rows = [], []
    mat = None
    LAST_MATRIX.clear()
    if collect_matrix:
        # Outbound trips per scenario, home node x destination node (for cycle_priorities.py).
        S = home.index.get_level_values(1).unique().values
        dn = set()
        for _, _, dest in streams(setup):
            dn.update(dest.index.get_level_values(1))
        if utility:
            pts_u, attract = setup_utility_cache(setup, nodes, G)
            for v in attract.values():
                dn.update(v.index)
        D = np.array(sorted(dn))
        s_idx = np.full(len(nodes), -1)
        s_idx[S] = np.arange(len(S))
        d_idx = np.full(len(nodes), -1)
        d_idx[D] = np.arange(len(D))
        mat = np.zeros((len(S), len(D), k_s), dtype=np.float32)
        LAST_MATRIX.clear()
        LAST_MATRIX.update(S=S, D=D, mat=mat, scenarios=list(scenarios), s_idx=s_idx, d_idx=d_idx)

    def as_arrays(table):
        return {k: (g.index.get_level_values(1).values, g.values / g.values.sum())
                for k, g in table.groupby(level=0)}

    for name, trips, dest in streams(setup):
        if only and name not in only:
            continue
        kind = STREAM_KIND[name]
        if verbose:
            print(f"{name}: {len(trips):,} SA2 pairs, {trips['trips'].sum():,.0f} trips, "
                  f"{trips['bike'].sum():,.0f} cycled")
        for direction in ("out", "back"):
            if collect_od and direction == "back" and scenarios == ():
                continue
            src_table, snk_table = (home, dest) if direction == "out" else (dest, home)
            key_src, key_snk = ("o", "d") if direction == "out" else ("d", "o")
            snk = as_arrays(snk_table)
            by_src = {k: (g[key_snk].values, g["trips"].values, g["bike"].values)
                      for k, g in trips.groupby(key_src)}
            src_nodes = src_table.index.get_level_values(1).unique()
            dist, pred = dijkstra(G, directed=True, indices=src_nodes.values, return_predecessors=True)
            for k, sn in enumerate(src_nodes):
                rows = src_table.xs(sn, level=1)
                dem = np.zeros((len(nodes), max(k_s, 1)))
                arc, V, depth = tree_paths(pred[k], sn, attrs, A)
                L, R, H, P = V[:, 0], V[:, 1], V[:, 2], V[:, 3]
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
                        if collect_od and direction == "out":
                            w = wsrc * wt
                            Lm = np.maximum(L[tn], 1)
                            od_rows.append((name, s2, other, w.sum(), (w * d_km).sum(), (w * grad).sum(),
                                            (w * H[tn] / Lm).sum(), (w * P[tn] / Lm).sum()))
                        cols = []
                        for sc in scenarios:
                            if sc == "census":
                                cols.append(bike)
                            elif sc == "local":
                                if kind == "commute":
                                    Lm = np.maximum(L[tn], 1)
                                    v = n_trips * uptake_local(d_km, grad, H[tn] / Lm, P[tn] / Lm)
                                    cols.append(np.maximum(v, bike))
                                else:
                                    cols.append(bike)
                            else:
                                v = n_trips * uptake(d_km, grad, ebike=sc == "ebike", model=model,
                                                     kind=kind)
                                cols.append(np.maximum(v, bike) if floor else v)
                        if cols:
                            np.add.at(dem, tn, np.column_stack(cols))
                            if mat is not None and direction == "out":
                                np.add.at(mat, (s_idx[sn], d_idx[tn]), np.column_stack(cols))
                        if direction == "out":
                            route_rows.append((name, n_trips.sum(), (n_trips * d_km).sum(),
                                               (n_trips * grad).sum(), *[c.sum() for c in cols]))
                if not scenarios or dem.sum() == 0:
                    continue
                acc = accumulate(pred[k], depth, dem)
                has = arc >= 0
                np.add.at(arc_flow, arc[has], acc[has])
            if verbose:
                print(f"  {direction}: routed from {len(src_nodes):,} origins")
    if utility and scenarios:
        u_flow, u_rows = run_utility(setup, G, A, arcs, attrs, nodes, scenarios, model, floor, verbose)
        arc_flow += u_flow
        route_rows += u_rows
    flow = np.zeros((len(e), k_s))
    np.add.at(flow, arcs["edge"].values, arc_flow)
    if collect_od:
        m = pd.DataFrame(od_rows, columns=["stream", "o", "d", "w", "km", "grad", "hs", "prot"])
        m = m.groupby(["stream", "o", "d"]).sum()
        for c in ("km", "grad", "hs", "prot"):
            m[c] = m[c] / m["w"]
        return flow, arc_flow, route_rows, arcs, e, setup, m.drop(columns="w").reset_index()
    return flow, arc_flow, route_rows, arcs, e, setup


def run_utility(setup, G, A, arcs, attrs, nodes, scenarios, model, floor, verbose, min_m=500, max_m=15000):
    """Shopping, visiting and leisure trips from homes (gravity model), routed and assigned.

    Returns (arc flows, summary rows). Return trips reuse the outbound route in reverse (the
    reverse arc where the street is two-way)."""
    home, dests, sa2s, roll, uni_sa2 = setup
    pts, attract = setup_utility_cache(setup, nodes, G)
    work = od("work", sa2s)
    base_share = (work.groupby("o")["bike"].sum() / work.groupby("o")["trips"].sum()).fillna(0)
    city_share = work["bike"].sum() / work["trips"].sum()
    residents = pts.groupby("node")["residents"].sum()
    sa2_of = pts.groupby("node")["sa2"].first()
    work_rate = work["trips"].sum() / pts.loc[pts["sa2"].isin(work["o"].unique()), "residents"].sum()
    src = residents[residents > 0].index.values
    dist, pred = dijkstra(G, directed=True, indices=src, return_predecessors=True)
    # Mean commute route length, for the gravity targets.
    mean_commute_km = 7.27
    rev = pd.Series(np.arange(len(arcs)), index=pd.MultiIndex.from_arrays([arcs["u"].values, arcs["v"].values]))
    rev_idx = rev.reindex(pd.MultiIndex.from_arrays([arcs["v"].values, arcs["u"].values])).values
    rev_idx = np.where(np.isnan(rev_idx), np.arange(len(arcs)), rev_idx).astype(int)
    k_s = len(scenarios)
    arc_flow = np.zeros((len(arcs), k_s))
    rows = []
    trees = [tree_paths(pred[k], sn, attrs, A) for k, sn in enumerate(src)]
    for purpose, cfg in UTILITY.items():
        a_nodes = attract[purpose].index.values
        a_w = attract[purpose].values.astype(float)
        target = cfg["length"] * mean_commute_km
        Ls = np.array([trees[k][1][a_nodes, 0] for k in range(len(src))]) / 1000
        ok = (Ls * 1000 >= min_m) & (Ls * 1000 <= max_m) & np.isfinite(dist[:, a_nodes])

        def mean_len(beta):
            w = np.where(ok, a_w * np.exp(-beta * Ls), 0)
            w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)
            return (w * np.where(ok, Ls, 0)).sum(axis=1).mean()
        lo, hi = 0.01, 3.0
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if mean_len(mid) > target else (lo, mid)
        beta = (lo + hi) / 2
        tot = dict(trips=0.0, km=0.0, grad=0.0, **{s: 0.0 for s in scenarios})
        for k, sn in enumerate(src):
            arc, V, depth = trees[k]
            w = np.where(ok[k], a_w * np.exp(-beta * Ls[k]), 0)
            if w.sum() == 0:
                continue
            n_i = residents[sn] * work_rate * cfg["rate"]
            trips = n_i * w / w.sum()
            L, R, H, P = V[a_nodes, 0], V[a_nodes, 1], V[a_nodes, 2], V[a_nodes, 3]
            d_km = L / 1000
            grad = np.where(L > 0, R / np.maximum(L, 1) * 100, 0)
            base = trips * base_share.get(sa2_of.get(sn), city_share) * cfg["factor"]
            cols = []
            for sc in scenarios:
                if sc == "census":
                    v = base
                elif sc == "local":
                    Lm = np.maximum(L, 1)
                    v = trips * uptake_local(d_km, grad, H / Lm, P / Lm) * cfg["factor"]
                else:
                    v = trips * uptake(d_km, grad, ebike=sc == "ebike", model=model) * cfg["factor"]
                cols.append(np.maximum(v, base) if (floor and sc != "census") else v)
            dem = np.zeros((len(nodes), k_s))
            np.add.at(dem, a_nodes, np.column_stack(cols))
            if LAST_MATRIX.get("mat") is not None and LAST_MATRIX["s_idx"][sn] >= 0:
                LAST_MATRIX["mat"][LAST_MATRIX["s_idx"][sn], LAST_MATRIX["d_idx"][a_nodes]] += np.column_stack(cols)
            acc = accumulate(pred[k], depth, dem)
            has = arc >= 0
            np.add.at(arc_flow, arc[has], acc[has])
            np.add.at(arc_flow, rev_idx[arc[has]], acc[has])
            tot["trips"] += trips.sum()
            tot["km"] += (trips * d_km).sum()
            tot["grad"] += (trips * grad).sum()
            for i, sc in enumerate(scenarios):
                tot[sc] += cols[i].sum()
        rows.append((purpose, tot["trips"], tot["km"], tot["grad"], *[tot[s] for s in scenarios]))
        if verbose:
            print(f"{purpose}: {tot['trips']:,.0f} trips (beta {beta:.2f}/km, mean {tot['km'] / tot['trips']:.1f} km), "
                  + ", ".join(f"{s} {tot[s] / tot['trips'] * 100:.1f}%" for s in scenarios))
    return arc_flow, rows


_UTIL_CACHE = {}


def setup_utility_cache(setup, nodes, G):
    key = id(setup)
    if key not in _UTIL_CACHE:
        pts, ep, sa2, sch = zones()
        keep_nodes = np.zeros(len(nodes), dtype=bool)
        ncomp, lab = connected_components(G, directed=True, connection="strong")
        keep_nodes[lab == np.bincount(lab).argmax()] = True
        pts = pts.copy()
        pts["node"] = snap(np.column_stack([pts.geometry.x, pts.geometry.y]), nodes, keep_nodes)[0]
        _UTIL_CACHE[key] = (pts, utility_destinations(pts, nodes, keep_nodes))
    return _UTIL_CACHE[key]


def main():
    params = load_params()
    print("route choice parameters:", params, "(calibrated)" if CALIBRATION.exists() else "(default)")
    scen = ["census", "godutch", "ebike"] + (["local"] if LOCAL.exists() else [])
    flow, arc_flow, route_rows, arcs, e, setup = run(params, scen)
    r = pd.DataFrame(route_rows, columns=["purpose", "trips", "km", "grad"] + scen).groupby("purpose").sum()
    r.loc["all"] = r.sum()
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
