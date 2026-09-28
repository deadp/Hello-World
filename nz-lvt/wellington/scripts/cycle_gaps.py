"""Rank where Wellington City most needs new protected cycle connections.

Inputs: cycle_edges.parquet (cycle_network.py) and cycle_flows.parquet (cycle_model.py).

A street edge is a gap when its potential cycling (Go Dutch scenario) is high but it is
stressful to ride (level of traffic stress 3-4) and has no protected facility. Gap edges on
the same street that touch each other form a corridor. Corridors are ranked by potential
cycle-km per weekday (trips x length) and labelled with the council's plan for them (WCC
Strategic Bike Network 2022): built or being built, planned (WCC or Let's Get Wellington
Moving stage), in the plan as "Primary desired" only, or not in the plan.

Writes outputs/tables/cycle_corridors.csv, outputs/tables/cycle_summary.csv,
outputs/blocks/cycle_edges.geojson (WGS84, for the web map) and
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
GAP_MIN_FLOW = 250  # Go Dutch trips per weekday
MAP_MIN_FLOW = 40
SCEN = ["census", "godutch", "ebike"]


def plan_status(stage, klass):
    """WCC Strategic Bike Network 2022 status. LGWM-staged links lost their programme when Let's
    Get Wellington Moving was disestablished in 2024, so they are shown separately."""
    if stage in ("Built", "Being built"):
        return "Built or underway"
    if not isinstance(stage, str) or not stage:
        return "Not in plan"
    if klass == "Primary desired":
        return "Desired only"
    return "Planned (ex-LGWM)" if "LGWM" in stage else "Planned (WCC)"


def load():
    e = gpd.read_parquet(PROC / "cycle_edges.parquet").join(pd.read_parquet(PROC / "cycle_flows.parquet"))
    city = gpd.read_file(RAW / "sa2.geojson").to_crs(2193).union_all()
    e = e[e.geometry.interpolate(0.5, normalized=True).within(city)].copy()
    e["plan"] = [plan_status(s, c) for s, c in zip(e["wcc_stage"], e["wcc_class"])]
    e["protected"] = e["facility"].isin(["separated", "path", "sidepath"])
    e["stress"] = np.where(e["lts"] >= 3, "high", "low")
    e["gap"] = (e["lts"] >= 3) & ~e["protected"] & (e["flow_godutch"] >= GAP_MIN_FLOW)
    return e


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
    # Merge runs of the same street broken by short protected or quiet stretches (< 150 m apart).
    runs = gpd.GeoDataFrame({"run": lab, "street": g["street"].values}, geometry=g.geometry.values, crs=2193)
    runs = runs.dissolve("run", aggfunc="first").rename_axis(None)
    zone = runs.copy()
    zone["geometry"] = runs.buffer(75)
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
        return pd.Series({
            "street": x["street"].iat[0],
            "suburbs": ", ".join(pd.Series(x["suburb"]).dropna().value_counts().index[:3]),
            "length_m": L.sum(),
            "godutch_cyc_km": (x["flow_godutch"] * L).sum() / 1000,
            "ebike_cyc_km": (x["flow_ebike"] * L).sum() / 1000,
            "census_cyc_km": (x["flow_census"] * L).sum() / 1000,
            "godutch_trips": (x["flow_godutch"] * L).sum() / L.sum(),
            "ebike_trips": (x["flow_ebike"] * L).sum() / L.sum(),
            "census_trips": (x["flow_census"] * L).sum() / L.sum(),
            "max_adt": x["adt"].max(),
            "speed": x["speed"].median(),
            "painted_share": (L * (x["facility"] == "painted")).sum() / L.sum(),
            "road": "State highway" if (x["highway"].isin(["trunk", "trunk_link"])).mean() > 0.5 else
                    x["highway"].mode().iat[0],
            "plan": plan_len.idxmax(),
            "plan_share": plan_len.max() / L.sum(),
        })

    c = g.groupby("corridor").apply(agg)
    c = c[c["length_m"] >= 60].sort_values("godutch_cyc_km", ascending=False)
    c["rank"] = np.arange(1, len(c) + 1)
    g["rank"] = g["corridor"].map(c["rank"])
    return c, g


def suburb_points():
    u = gpd.read_parquet(PROC / "units.parquet")[["Suburb", "geometry"]]
    return gpd.GeoDataFrame(u[["Suburb"]], geometry=u.geometry.representative_point(), crs=2193)


def summary(e):
    rows = {}
    for s in SCEN:
        km = e[f"flow_{s}"] * e["length_m"] / 1000
        rows[s] = {
            "cycle_km_per_day": km.sum(),
            "on_protected_%": km[e["protected"]].sum() / km.sum() * 100,
            "on_quiet_streets_%": km[~e["protected"] & (e["lts"] <= 2)].sum() / km.sum() * 100,
            "on_high_stress_%": km[e["lts"] >= 3].sum() / km.sum() * 100,
            "on_high_stress_painted_%": km[(e["lts"] >= 3) & (e["facility"] == "painted")].sum() / km.sum() * 100,
        }
    return pd.DataFrame(rows).T


def export(e, g):
    m = e[(e["flow_godutch"] >= MAP_MIN_FLOW) | e["protected"] | e["plan"].ne("Not in plan")].copy()
    m["rank"] = g["rank"].reindex(m.index)
    m = m[["geometry", "name", "flow_census", "flow_godutch", "flow_ebike", "lts", "facility", "plan", "adt",
           "speed", "gap", "rank"]]
    for s in SCEN:
        m[f"flow_{s}"] = m[f"flow_{s}"].round(0)
    m["geometry"] = m.geometry.simplify(2)
    m = m.to_crs(4326)
    m.to_file(BLOCKS / "cycle_edges.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
    print(f"map edges: {len(m):,}, {(BLOCKS / 'cycle_edges.geojson').stat().st_size / 1e6:.1f} MB")


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
          "2023 census work and education trips. Line width = trips per weekday. Numbers = top corridors.")
    fig.subplots_adjust(top=0.93, bottom=0.01, left=0.01, right=0.99)
    fig.savefig(FIGS / "cycle_gaps.png", dpi=150)
    plt.close(fig)


def main():
    e = load()
    s = summary(e)
    s.round(1).to_csv(TABLES / "cycle_summary.csv")
    print(s.round(1).to_string())
    c, g = corridors(e)
    c.round(2).to_csv(TABLES / "cycle_corridors.csv", index=False)
    pd.set_option("display.width", 250)
    print(c.head(25)[["rank", "street", "road", "suburbs", "length_m", "godutch_trips", "ebike_trips", "census_trips",
                      "godutch_cyc_km", "max_adt", "painted_share", "plan", "plan_share"]].round(1).to_string())
    print(c.groupby("plan")["godutch_cyc_km"].sum().round(0).to_string())
    print("top 20 by plan:", c.head(20)["plan"].value_counts().to_dict())
    print(c[c["plan"] == "Not in plan"].head(12)[["rank", "street", "road", "suburbs", "length_m", "godutch_trips",
                                                  "max_adt"]].round(0).to_string())
    export(e, g)
    figure(e, c)


if __name__ == "__main__":
    main()
