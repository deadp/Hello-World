"""Points on the cycle network worth checking against aerial imagery or on the ground.

Two kinds, written to outputs/blocks/cycle_review.geojson (WGS84) and
outputs/tables/cycle_review.csv:

  missing_link   a cycleway, path or protected lane that dead-ends within 20 m of another street
                 or path it does not join, where getting there by the network takes over 200 m
                 and at least 5x the straight line. Usually a mapping gap in OpenStreetMap (a path
                 not joined to the road it meets), sometimes a real missing link (kerb, barrier,
                 steps). Either way the model can't use it.
  protected_ends where a protected facility stops and riders continue on a busy street (stress
                 3-4) with nothing better nearby: the ends of today's protected routes.

Both are ranked by Go Dutch trips on the edge that ends there.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
TABLES = ROOT / "outputs" / "tables"
BLOCKS = ROOT / "outputs" / "blocks"
PROTECTED = {"track", "protected_lane", "sidepath", "seg_path", "shared_path", "shared_footway"}
NEAR_M, DETOUR_MIN_M, DETOUR_RATIO = 20, 200, 5


def main():
    e = gpd.read_parquet(PROC / "cycle_edges.parquet").join(pd.read_parquet(PROC / "cycle_flows.parquet"))
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    city = gpd.read_file(RAW / "sa2.geojson").to_crs(2193).union_all()
    n = len(nodes)
    deg = pd.Series(np.concatenate([e["u"], e["v"]])).value_counts().reindex(range(n), fill_value=0)
    G = csr_matrix((np.r_[e["length_m"], e["length_m"]].clip(0.5), (np.r_[e["u"], e["v"]], np.r_[e["v"], e["u"]])),
                   shape=(n, n))
    bikey = e["highway"].isin(["cycleway", "path", "footway", "pedestrian", "track", "bridleway"]) | \
        e["facility"].isin(PROTECTED)
    rows = []

    # 1. Dead ends of bike paths near a street or path they don't join.
    ends = []
    for col in ("u", "v"):
        m = bikey & (deg.reindex(e[col]).values == 1)
        ends.append(pd.DataFrame({"node": e.loc[m, col].values, "edge": e.index[m]}))
    ends = pd.concat(ends).drop_duplicates("node")
    seg = e.geometry.segmentize(5).get_coordinates(index_parts=False)
    tree = cKDTree(seg.values)
    xy = nodes.loc[ends["node"], ["x", "y"]].values
    for (node, edge), p in zip(ends[["node", "edge"]].values, xy):
        near = tree.query_ball_point(p, NEAR_M)
        cand = pd.Index(seg.index[near]).unique().difference([edge])
        cand = cand[(e.loc[cand, "u"] != node) & (e.loc[cand, "v"] != node)]
        if len(cand) == 0:
            continue
        # Network distance to the nearest candidate edge's closer end.
        targets = np.unique(np.r_[e.loc[cand, "u"].values, e.loc[cand, "v"].values])
        d_net = dijkstra(G, indices=node, limit=5000)[targets]
        d_str = np.hypot(*(nodes.loc[targets, ["x", "y"]].values - p).T)
        k = np.argmin(d_str)
        if d_net.min() > max(DETOUR_MIN_M, DETOUR_RATIO * d_str.min()):
            other = e.loc[cand].sort_values("length_m", ascending=False).iloc[0]
            rows.append(dict(kind="missing_link", x=p[0], y=p[1], name=e.at[edge, "name"],
                             other=other["name"] or other["highway"], highway=e.at[edge, "highway"],
                             gap_m=float(d_str[k]), detour_m=float(d_net.min()) if np.isfinite(d_net.min()) else None,
                             trips=float(e.at[edge, "flow_godutch"])))

    # 2. Where protected routes end onto a busy street.
    prot = e["facility"].isin(PROTECTED)
    pn = set(e.loc[prot, "u"]) | set(e.loc[prot, "v"])
    inc = pd.concat([e[["u", "lts", "facility", "name", "flow_godutch"]].rename(columns={"u": "node"}),
                     e[["v", "lts", "facility", "name", "flow_godutch"]].rename(columns={"v": "node"})])
    inc = inc[inc["node"].isin(pn)]
    g = inc.groupby("node").agg(prot=("facility", lambda f: f.isin(PROTECTED).sum()),
                                calm=("lts", lambda x: (x <= 2).sum()), busy=("lts", lambda x: (x >= 3).sum()),
                                n=("lts", "size"))
    # A protected edge arrives, no other protected edge or calm street continues, a busy one does.
    stop = g[(g["prot"] == 1) & (g["busy"] >= 1) & (g["calm"] - g["prot"] <= 0)].index
    for node in stop:
        x = inc[inc["node"] == node]
        pe = x[x["facility"].isin(PROTECTED)].iloc[0]
        be = x[x["lts"] >= 3].sort_values("flow_godutch", ascending=False).iloc[0]
        rows.append(dict(kind="protected_ends", x=nodes.at[node, "x"], y=nodes.at[node, "y"], name=pe["name"],
                         other=be["name"], highway=None, gap_m=None, detour_m=None,
                         trips=float(pe["flow_godutch"])))

    r = pd.DataFrame(rows)
    pts = gpd.GeoDataFrame(r, geometry=gpd.points_from_xy(r["x"], r["y"]), crs=2193)
    pts = pts[pts.within(city)].sort_values("trips", ascending=False)
    pts = pts[(pts["kind"] == "missing_link") | (pts["trips"] >= 50)].drop(columns=["x", "y"])
    pts.drop(columns="geometry").round(1).to_csv(TABLES / "cycle_review.csv", index=False)
    pts.to_crs(4326).to_file(BLOCKS / "cycle_review.geojson", driver="GeoJSON")
    print(pts["kind"].value_counts().to_dict())
    pd.set_option("display.width", 200)
    for k in ("missing_link", "protected_ends"):
        print(pts[pts["kind"] == k].head(15).drop(columns="geometry").round(0).to_string())


if __name__ == "__main__":
    main()
