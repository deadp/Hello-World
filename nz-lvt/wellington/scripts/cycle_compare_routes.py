"""Compare real ridden routes (Ride with GPS, Strava, etc.) with the modelled cycle network.

Put GPX, KML or GeoJSON exports in data/raw/cycling/user_routes/ (or pass files as arguments), or
Ride with GPS route/trip ids or links (public ones download as GPX). For every 10 m along each
route it finds the nearest network edge and reports:

  off_network   stretches over 30 m long where the route is more than 20 m from any street or
                path in the model: a path, cut-through or link the model is missing (or a GPS
                wobble; check the map).
  busy          stretches the route rides on edges the model rates stress 3-4 in that direction
                (people do ride them: either they're calmer than rated, or they're the only way).
  one_way       stretches ridden against a one-way street the model doesn't allow bikes on
                (contraflow, a missing oneway:bicycle=no tag, or a path alongside).

Writes outputs/tables/cycle_route_check.csv and outputs/blocks/cycle_route_check.geojson (WGS84).
"""

import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
SRC = ROOT / "data" / "raw" / "cycling" / "user_routes"
TABLES = ROOT / "outputs" / "tables"
BLOCKS = ROOT / "outputs" / "blocks"
STEP, NEAR, MIN_RUN = 10, 20, 30


def fetch_rwgps(ref):
    """Download a public Ride with GPS route or trip as GPX; returns the saved path."""
    m = re.search(r"(routes|trips)/(\d+)", ref) or re.fullmatch(r"(\d+)", ref)
    kind, rid = (m.group(1), m.group(2)) if m and m.lastindex == 2 else ("routes", ref)
    SRC.mkdir(parents=True, exist_ok=True)
    path = SRC / f"rwgps_{kind}_{rid}.gpx"
    r = requests.get(f"https://ridewithgps.com/{kind}/{rid}.gpx", params={"sub_format": "track"}, timeout=120)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path


def read_lines(path):
    layers = ["tracks", "routes"] if path.suffix.lower() == ".gpx" else [None]
    out = []
    for layer in layers:
        try:
            g = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
        except Exception:
            continue
        g = g[g.geometry.notna() & g.geom_type.isin(["LineString", "MultiLineString"])]
        if len(g):
            g = g.explode(index_parts=False).to_crs(2193)
            out += [(path.stem, geom) for geom in g.geometry]
    return out


def main(args):
    files = []
    for a in args:
        files.append(fetch_rwgps(a) if not Path(a).exists() else Path(a))
    if not args:
        files = sorted(p for p in SRC.glob("*") if p.suffix.lower() in (".gpx", ".kml", ".geojson", ".json"))
    lines = [x for f in files for x in read_lines(f)]
    if not lines:
        sys.exit(f"no routes found; put GPX/KML/GeoJSON files in {SRC} or pass Ride with GPS links")
    e = gpd.read_parquet(PROC / "cycle_edges.parquet").join(pd.read_parquet(PROC / "cycle_flows.parquet"))
    rows = []
    for name, geom in lines:
        d = np.arange(0, geom.length, STEP)
        pts = gpd.GeoDataFrame({"route": name, "at": d}, geometry=[geom.interpolate(x) for x in d], crs=2193)
        j = gpd.sjoin_nearest(pts, e[["geometry", "name", "highway", "lts_fw", "lts_bw", "can_fw", "can_bw",
                                        "facility"]], how="left", distance_col="dist")
        j = j[~j.index.duplicated()]
        # Direction of travel against the edge's drawn direction.
        nxt = pts.geometry.shift(-1).fillna(pts.geometry)
        heading = np.c_[nxt.x - pts.geometry.x, nxt.y - pts.geometry.y]
        eg = e.geometry.loc[j["index_right"]].values
        ev = np.array([(g.coords[-1][0] - g.coords[0][0], g.coords[-1][1] - g.coords[0][1]) for g in eg])
        fw = (heading * ev).sum(axis=1) >= 0
        lts = np.where(fw, j["lts_fw"], j["lts_bw"])
        allowed = np.where(fw, j["can_fw"], j["can_bw"])
        near = j["dist"].values <= NEAR
        j["flag"] = np.where(~near, "off_network", np.where(~allowed.astype(bool), "one_way",
                                                              np.where(lts >= 3, "busy", "")))
        # Runs of the same flag.
        run = (j["flag"] != j["flag"].shift()).cumsum()
        for _, r in j[j["flag"] != ""].groupby(run[j["flag"] != ""]):
            if len(r) * STEP < MIN_RUN:
                continue
            rows.append(dict(route=name, flag=r["flag"].iat[0], length_m=len(r) * STEP,
                             street=r["name"].mode().iat[0] if r["name"].notna().any() else None,
                             highway=r["highway"].mode().iat[0], max_gap_m=round(r["dist"].max()),
                             geometry=LineString(list(r.geometry.apply(lambda p: (p.x, p.y))))
                             if len(r) > 1 else r.geometry.iat[0].buffer(1)))
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=2193) if rows else gpd.GeoDataFrame(
        columns=["route", "flag", "length_m", "geometry"], geometry="geometry", crs=2193)
    out.drop(columns="geometry").to_csv(TABLES / "cycle_route_check.csv", index=False)
    out.to_crs(4326).to_file(BLOCKS / "cycle_route_check.geojson", driver="GeoJSON")
    print(f"{len(lines)} route lines from {len(files)} files; flagged stretches:")
    print(out.groupby("flag")["length_m"].agg(["size", "sum"]).to_string() if len(out) else "none")
    pd.set_option("display.width", 200)
    print(out.drop(columns="geometry").sort_values("length_m", ascending=False).head(30).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
