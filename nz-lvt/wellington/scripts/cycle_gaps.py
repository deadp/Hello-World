"""Rank where Wellington City most needs new protected cycle connections.

Inputs: cycle_edges.parquet (cycle_network.py) and cycle_flows.parquet (cycle_model.py).

Direction matters: WCC's transitional cycleways often protect the uphill side only. Each edge is
judged per direction of travel. A direction is a gap when it is stressful to ride (level of
traffic stress 3-4 in that direction, so no protection) and carries >= 60 potential trips a
weekday, on an edge with >= 250 potential trips in total (Go Dutch scenario). Gap edges on the
same street that touch each other (or are < 300 m apart) form a corridor. Corridors are ranked by
potential cycle-km per weekday on their stressful directions and labelled with the council's plan
(WCC Strategic Bike Network 2022): built or being built, planned (council stage), unfunded
(staged under Let's Get Wellington Moving, disestablished in December 2023), "Primary desired"
only, or not in the plan. A corridor where only one direction is a gap is flagged "one way".

Crossings: junction nodes where potential trips arrive on a low-stress street and must cross a
level 3-4 road without signals or a marked crossing, ranked by those trips.

Writes outputs/tables/cycle_corridors.csv, cycle_crossings.csv, cycle_summary.csv,
outputs/blocks/cycle_edges.geojson and cycle_points.geojson (WGS84, for the web map) and
outputs/figures/cycle_gaps.png.
"""

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.sparse import coo_matrix  # noqa: E402
from scipy.sparse.csgraph import connected_components  # noqa: E402

from figures import INK, INK2, MUTED, title  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
TABLES = ROOT / "outputs" / "tables"
BLOCKS = ROOT / "outputs" / "blocks"
FIGS = ROOT / "outputs" / "figures"
GAP_MIN_FLOW = 250  # Go Dutch trips per weekday, both directions
GAP_MIN_DIR = 60  # in the stressful direction
PROTECTED = {"track", "protected_lane", "sidepath", "seg_path", "shared_path", "shared_footway"}
LANE = {"painted_lane", "buffered_lane", "bus_lane"}
MAP_MIN_FLOW = 40
SCEN = ["census", "godutch", "ebike", "local"]


def plan_status(stage, klass):
    """WCC Strategic Bike Network 2022 status. LGWM-staged links lost their programme when Let's
    Get Wellington Moving was disestablished (December 2023), so they are shown as unfunded."""
    if stage in ("Built", "Being built"):
        return "Built or underway"
    if not isinstance(stage, str) or not stage:
        return "Not in plan"
    if klass == "Primary desired":
        return "Desired only"
    return "Unfunded (ex-LGWM)" if "LGWM" in stage else "Planned (WCC)"


def load():
    e_all = gpd.read_parquet(PROC / "cycle_edges.parquet").join(pd.read_parquet(PROC / "cycle_flows.parquet"))
    city = gpd.read_file(RAW / "sa2.geojson").to_crs(2193).union_all()
    e = e_all[e_all.geometry.interpolate(0.5, normalized=True).within(city)].copy()
    e["plan"] = [plan_status(s, c) for s, c in zip(e["wcc_stage"], e["wcc_class"])]
    e["protected"] = e["facility"].isin(PROTECTED)
    for d in ("fw", "bw"):
        e[f"hs_{d}"] = e[f"can_{d}"] & (e[f"lts_{d}"] >= 3)
        e[f"gapd_{d}"] = e[f"hs_{d}"] & (e[f"flow_godutch_{d}"] >= GAP_MIN_DIR)
    e["gap"] = (e["flow_godutch"] >= GAP_MIN_FLOW) & (e["gapd_fw"] | e["gapd_bw"])
    two_way = e["can_fw"] & e["can_bw"]
    e["gap_dir"] = np.where(~e["gap"], "", np.where(e["gapd_fw"] & e["gapd_bw"] | ~two_way, "both", "one way"))
    for s in SCEN:
        e[f"hs_flow_{s}"] = e[f"flow_{s}_fw"] * e["hs_fw"] + e[f"flow_{s}_bw"] * e["hs_bw"]
    e["hs_fac"] = np.where(e["hs_fw"], e["fac_fw"], e["fac_bw"])
    return e_all, e


def corridors(e):
    g = e[e["gap"]].copy()
    g["street"] = g["name"].fillna("(unnamed link)")
    # Connected runs of gap edges on the same street.
    nodes = pd.Index(pd.unique(np.concatenate([g["u"].values, g["v"].values])))
    iu, iv = nodes.get_indexer(g["u"]), nodes.get_indexer(g["v"])
    ei = np.arange(len(g))
    # Edge adjacency via shared nodes, restricted to the same street.
    inc = pd.DataFrame({"edge": np.concatenate([ei, ei]), "node": np.concatenate([iu, iv]),
                        "street": np.concatenate([g["street"].values, g["street"].values])})
    pairs = inc.merge(inc, on=["node", "street"])
    adj = coo_matrix((np.ones(len(pairs)), (pairs["edge_x"], pairs["edge_y"])), shape=(len(g), len(g)))
    _, lab = connected_components(adj, directed=False)
    # Merge runs of the same street broken by short protected or quiet stretches (< 300 m apart).
    runs = gpd.GeoDataFrame({"run": lab, "street": g["street"].values}, geometry=g.geometry.values, crs=2193)
    runs = runs.dissolve("run", aggfunc="first").rename_axis(None)
    zone = runs.copy()
    zone["geometry"] = runs.buffer(150)
    pairs = gpd.sjoin(zone, zone, predicate="intersects")
    pairs = pairs[pairs["street_left"] == pairs["street_right"]]
    m = coo_matrix((np.ones(len(pairs)), (pairs.index.values, pairs["index_right"].values)),
                   shape=(lab.max() + 1, lab.max() + 1))
    _, merged = connected_components(m, directed=False)
    g["corridor"] = merged[lab]
    mid = gpd.GeoDataFrame(geometry=g.geometry.interpolate(0.5, normalized=True), index=g.index, crs=2193)
    j = gpd.sjoin_nearest(mid, suburb_points(), how="left", max_distance=300)
    g["suburb"] = j[~j.index.duplicated()]["Suburb"].reindex(g.index)

    def agg(x):
        L = x["length_m"]
        plan_len = L.groupby(x["plan"]).sum()
        fac_len = L.groupby(x["hs_fac"]).sum()
        return pd.Series({
            "street": x["street"].iat[0],
            "suburbs": ", ".join(pd.Series(x["suburb"]).dropna().value_counts().index[:3]),
            "length_m": L.sum(),
            "godutch_cyc_km": (x["hs_flow_godutch"] * L).sum() / 1000,
            "ebike_cyc_km": (x["hs_flow_ebike"] * L).sum() / 1000,
            "census_cyc_km": (x["hs_flow_census"] * L).sum() / 1000,
            "local_cyc_km": (x["hs_flow_local"] * L).sum() / 1000,
            "godutch_trips": (x["flow_godutch"] * L).sum() / L.sum(),
            "ebike_trips": (x["flow_ebike"] * L).sum() / L.sum(),
            "census_trips": (x["flow_census"] * L).sum() / L.sum(),
            "max_adt": x["adt"].max(),
            "speed": x["speed"].median(),
            "facility_now": fac_len.idxmax(),
            "painted_share": (L * x["hs_fac"].isin(LANE)).sum() / L.sum(),
            "one_way_share": (L * (x["gap_dir"] == "one way")).sum() / L.sum(),
            "road": "State highway" if (x["highway"].isin(["trunk", "trunk_link"])).mean() > 0.5 else
                    x["highway"].mode().iat[0],
            "plan": plan_len.idxmax(),
            "plan_share": plan_len.max() / L.sum(),
        })

    c = g.groupby("corridor").apply(agg)
    c = c[c["length_m"] >= 60].sort_values("godutch_cyc_km", ascending=False)
    c["rank"] = np.arange(1, len(c) + 1)
    # Robustness: rank the same corridors under the census flows and the Wellington-fitted model.
    for s in ("local", "census"):
        c[f"rank_{s}"] = c[f"{s}_cyc_km"].rank(ascending=False, method="min").astype(int)
    g["rank"] = g["corridor"].map(c["rank"])
    return c, g


def suburb_points():
    u = gpd.read_parquet(PROC / "units.parquet")[["Suburb", "geometry"]]
    return gpd.GeoDataFrame(u[["Suburb"]], geometry=u.geometry.representative_point(), crs=2193)


def summary(e):
    rows = {}
    for s in SCEN:
        tot = prot = quiet = hs = lane = 0.0
        for d in ("fw", "bw"):
            km = e[f"flow_{s}_{d}"] * e["length_m"] / 1000
            p = e[f"fac_{d}"].isin(PROTECTED)
            tot += km.sum()
            prot += km[p].sum()
            quiet += km[~p & (e[f"lts_{d}"] <= 2)].sum()
            hs += km[e[f"lts_{d}"] >= 3].sum()
            lane += km[(e[f"lts_{d}"] >= 3) & e[f"fac_{d}"].isin(LANE)].sum()
        rows[s] = {"cycle_km_per_day": tot, "on_protected_%": prot / tot * 100, "on_quiet_streets_%": quiet / tot * 100,
                   "on_high_stress_%": hs / tot * 100, "on_high_stress_painted_%": lane / tot * 100}
    return pd.DataFrame(rows).T


def crossings(e_all, e):
    """Junctions where low-stress approaches meet an unsignalised level 3-4 road."""
    arr = pd.concat([
        pd.DataFrame({"node": e_all["v"], "flow": e_all["flow_godutch_fw"], "census": e_all["flow_census_fw"],
                      "ok": e_all["can_fw"] & (e_all["lts_fw"] <= 2) & (e_all["cross_v"] >= 3),
                      "approach": e_all["name"]}),
        pd.DataFrame({"node": e_all["u"], "flow": e_all["flow_godutch_bw"], "census": e_all["flow_census_bw"],
                      "ok": e_all["can_bw"] & (e_all["lts_bw"] <= 2) & (e_all["cross_u"] >= 3),
                      "approach": e_all["name"]})])
    arr = arr[arr["ok"] & (arr["flow"] > 0)]
    n = arr.groupby("node").agg(trips=("flow", "sum"), census=("census", "sum"),
                                approach=("approach", lambda x: ", ".join(pd.Series(x).dropna().unique()[:2])))
    busy = e_all[e_all["lts"] >= 3]
    inc = pd.concat([busy[["u", "name", "lts", "adt", "speed"]].rename(columns={"u": "node"}),
                     busy[["v", "name", "lts", "adt", "speed"]].rename(columns={"v": "node"})])
    road = inc.sort_values("adt", ascending=False).drop_duplicates("node").set_index("node")
    n = n.join(road.rename(columns={"name": "crossing"}), how="inner")
    n = n[n["crossing"].notna()]  # unnamed pieces are mostly slip lanes and split carriageways
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    pts = gpd.GeoDataFrame(n, geometry=gpd.points_from_xy(nodes.loc[n.index, "x"], nodes.loc[n.index, "y"]), crs=2193)
    city = gpd.read_file(RAW / "sa2.geojson").to_crs(2193).union_all()
    pts = pts[pts.within(city)].sort_values("trips", ascending=False)
    j = gpd.sjoin_nearest(pts[["geometry"]], suburb_points(), how="left", max_distance=300)
    pts["suburb"] = j[~j.index.duplicated()]["Suburb"].reindex(pts.index)
    pts["rank"] = np.arange(1, len(pts) + 1)
    return pts


def export(e, g, xing):
    # Main roads always, so streets are not drawn broken where a short piece carries few trips.
    major = e["highway"].isin(["primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
                               "trunk", "trunk_link"])
    m = e[(e["flow_godutch"] >= MAP_MIN_FLOW) | e["protected"] | e["plan"].ne("Not in plan") | major].copy()
    m["rank"] = g["rank"].reindex(m.index)
    m = m[["geometry", "name", "flow_census", "flow_godutch", "flow_ebike", "flow_local", "flow_godutch_fw",
           "flow_godutch_bw",
           "lts", "lts_fw", "lts_bw", "can_fw", "can_bw", "facility", "fac_fw", "fac_bw", "plan", "adt", "speed",
           "gap", "gap_dir", "rank"]]
    for c in m.columns:
        if c.startswith("flow_"):
            m[c] = m[c].round(0)
    m["geometry"] = m.geometry.simplify(2)
    m = m.to_crs(4326)
    m.to_file(BLOCKS / "cycle_edges.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
    print(f"map edges: {len(m):,}, {(BLOCKS / 'cycle_edges.geojson').stat().st_size / 1e6:.1f} MB")
    pts = xing.head(40).copy()
    pts["kind"] = "crossing"
    pts = pts[["kind", "rank", "trips", "census", "approach", "crossing", "lts", "adt", "speed", "suburb", "geometry"]]
    cnt = counters()
    frames = [pts.to_crs(4326)] + ([cnt.to_crs(4326)] if cnt is not None else [])
    out = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=4326)
    out.to_file(BLOCKS / "cycle_points.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)


def counters():
    """Counter sites with counted and modelled weekday cyclists (from cycle_validate.py)."""
    f = TABLES / "cycle_counters.csv"
    if not f.exists():
        return None
    c = pd.read_csv(f)
    meta = pd.read_csv(RAW / "cycling" / "sensors" / "meta.csv").set_index("COUNTLINE_ID")
    s = c.groupby(["COUNTLINE_ID", "NAME"]).agg(counted=("weekday_avg", "sum"),
                                                modelled=("modelled_census", "sum")).reset_index()
    s = s.join(meta[["LATITUDE_START_LINE", "LONGITUDE_START_LINE"]], on="COUNTLINE_ID")
    s["kind"] = "counter"
    s = s.rename(columns={"NAME": "approach"})
    return gpd.GeoDataFrame(s[["kind", "approach", "counted", "modelled"]], crs=4326,
                            geometry=gpd.points_from_xy(s["LONGITUDE_START_LINE"], s["LATITUDE_START_LINE"])).to_crs(2193)


def figure(e, c):
    xmin, ymin, xmax, ymax = 1745000, 5421000, 1756500, 5443800
    fig, ax = plt.subplots(figsize=(9, 14))
    base = e[e["flow_godutch"] >= MAP_MIN_FLOW]
    w = np.sqrt(base["flow_godutch"].clip(upper=6000)) / 18
    colours = np.where(base["protected"], "#1a9850", np.where(base["lts"] <= 2, "#9aa5ad",
                                                             np.where(base["gap"], "#d73027", "#f4a582")))
    base.plot(ax=ax, color=colours, linewidth=w)
    top = e[e["gap"]].copy()
    for _, r in c.head(15).iterrows():
        seg = top[(top["name"].fillna("(unnamed link)") == r["street"])]
        if len(seg):
            p = seg.geometry.union_all().centroid
            ax.annotate(str(int(r["rank"])), (p.x, p.y), fontsize=8, fontweight="bold", color="white",
                        ha="center", va="center",
                        bbox=dict(boxstyle="circle,pad=0.25", fc=INK, ec="none"))
    for col, lab in [("#1a9850", "Protected (separated lane, path or parallel path)"), ("#9aa5ad", "Quiet street"),
                     ("#f4a582", "Busy street, lower potential"), ("#d73027", "Gap: busy, unprotected, high potential")]:
        ax.plot([], [], color=col, lw=3, label=lab)
    ax.legend(loc="lower left", frameon=False, fontsize=9)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_axis_off()
    title(ax, "Where Wellington needs protected cycle connections",
          "Potential cycling if the city cycled like the Netherlands (PCT Go Dutch, hill-adjusted),\n"
          "2023 census work and education trips plus shopping, visiting and leisure trips.\n"
          "Line width = trips per weekday. Numbers = top corridors.")
    fig.subplots_adjust(top=0.93, bottom=0.01, left=0.01, right=0.99)
    fig.savefig(FIGS / "cycle_gaps.png", dpi=150)
    plt.close(fig)


def main():
    e_all, e = load()
    s = summary(e)
    s.round(1).to_csv(TABLES / "cycle_summary.csv")
    print(s.round(1).to_string())
    c, g = corridors(e)
    c.round(2).to_csv(TABLES / "cycle_corridors.csv", index=False)
    pd.set_option("display.width", 250)
    print(c.head(25)[["rank", "street", "road", "suburbs", "length_m", "godutch_trips", "census_trips",
                      "godutch_cyc_km", "max_adt", "speed", "facility_now", "one_way_share", "plan"]].round(2).to_string())
    print(c.groupby("plan")["godutch_cyc_km"].sum().round(0).to_string())
    print("top 20 by plan:", c.head(20)["plan"].value_counts().to_dict())
    from scipy.stats import spearmanr
    for s in ("local", "census"):
        top = set(c[c["rank"] <= 20].index)
        alt = set(c[c[f"rank_{s}"] <= 20].index)
        print(f"ranking vs {s}: spearman {spearmanr(c['godutch_cyc_km'], c[f'{s}_cyc_km']).statistic:.2f}, "
              f"top-20 overlap {len(top & alt)}/20")
    xing = crossings(e_all, e)
    xing.drop(columns="geometry").head(40).round(0).to_csv(TABLES / "cycle_crossings.csv")
    print(xing.head(15)[["trips", "census", "approach", "crossing", "lts", "adt", "speed", "suburb"]].round(0).to_string())
    export(e, g, xing)
    figure(e, c)


if __name__ == "__main__":
    main()
