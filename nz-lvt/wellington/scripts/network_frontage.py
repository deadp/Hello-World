"""Attribute local road and pipe network to the parcels it serves (Urban3-style frontage).

For every local network segment we drop sample points every STEP metres, offset them
OFFSET metres to each side of the line, and give each point's share of the segment to the
nearest parcel within MAX_DIST metres. A street therefore splits its length between the
properties on both sides, in proportion to their frontage.

Local vs shared network
  roads  local: Local, Residential, Sub-Collector, Rural 1-3, Service Lane, Suburban
         Shopping, Central City Shopping/Business/Golden Mile, Pedestrian Mall, Carpark.
         shared (citywide): Collector, Principal, Arterial. Excluded: State Highway,
         Motorway, ramps, private, unformed, proposed and non-transport roads, walkways.
  pipes  local: council-owned mains, rider mains, service connections, sump leads and
         culverts up to 300 mm. Trunk mains and larger pipes are shared citywide.
Pipe length is weighted by a replacement-cost factor that rises with diameter
(1.0 at <=150 mm, 1.3 at 151-225 mm, 1.7 at 226-300 mm), a common shape for reticulation
unit rates.

Writes data/processed/parcel_frontage.parquet with, per parcel polygon:
  road_local_m, road_shared_m, water_local_w, wastewater_local_w, stormwater_local_w
plus citywide totals of the shared network in data/processed/network_totals.json.
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString

ROOT = Path(__file__).resolve().parents[1]
NET = ROOT / "data" / "raw" / "networks"
PROC = ROOT / "data" / "processed"

STEP, OFFSET, MAX_DIST = 10.0, 8.0, 45.0
ROAD_LOCAL = {"Local", "Residential", "Sub-Collector", "Rural 1", "Rural 2", "Rural 3", "Service Lane",
              "Suburban Shopping", "Central City Shopping", "Central City Business", "Central City Golden Mile",
              "Pedestrian Mall", "Carpark", "Tawa Driveway"}
ROAD_SHARED = {"Collector", "Principal", "Arterial"}
PIPE_LOCAL_TYPES = {"Main", "RIderMain", "Service Connection", "Sump Lead", "Culvert"}


def diameter_factor(d):
    d = pd.to_numeric(d, errors="coerce").fillna(150)
    return np.select([d <= 150, d <= 225, d <= 300], [1.0, 1.3, 1.7], default=np.nan)


def sample_points(lines, weight):
    """Points every STEP m, offset both sides; each carries weight * segment_share."""
    xs, ys, ws = [], [], []
    for geom, w in zip(lines.geometry, weight):
        parts = geom.geoms if isinstance(geom, MultiLineString) else [geom]
        for part in parts:
            n = max(int(part.length // STEP), 1)
            per = w * part.length / n / 2  # two sides
            for side in (OFFSET, -OFFSET):
                try:
                    off = part.parallel_offset(abs(side), "left" if side > 0 else "right")
                except Exception:
                    off = part
                if off.is_empty:
                    off = part
                if not isinstance(off, (LineString, MultiLineString)):
                    off = part
                for i in range(n):
                    p = off.interpolate((i + 0.5) / n, normalized=True)
                    xs.append(p.x)
                    ys.append(p.y)
                    ws.append(per)
    return gpd.GeoDataFrame({"w": ws}, geometry=gpd.points_from_xy(xs, ys), crs=2193)


def attribute(points, parcels, col):
    j = gpd.sjoin_nearest(points, parcels[["pid", "geometry"]], how="left", max_distance=MAX_DIST)
    j = j[~j.index.duplicated()]
    got = j.groupby("pid")["w"].sum()
    lost = j.loc[j["pid"].isna(), "w"].sum()
    print(f"  {col}: attributed {got.sum():,.0f}, unmatched {lost:,.0f} ({lost / (got.sum() + lost):.1%})")
    return got.rename(col)


def main():
    u = gpd.read_parquet(PROC / "units.parquet")  # all parcels, incl. non-rateable (parks, schools)
    u["wkb"] = u.geometry.to_wkb()
    parcels = u.drop_duplicates("wkb")[["wkb", "geometry"]].reset_index(drop=True)
    parcels["pid"] = parcels.index

    roads = gpd.read_parquet(NET / "roads.parquet")
    totals = {}
    out = []
    local = roads[roads["category"].isin(ROAD_LOCAL)]
    shared = roads[roads["category"].isin(ROAD_SHARED)]
    totals["road_local_km"] = local.length.sum() / 1000
    totals["road_shared_km"] = shared.length.sum() / 1000
    print("roads")
    out.append(attribute(sample_points(local, np.ones(len(local))), parcels, "road_local_m"))

    for net in ["water", "wastewater", "stormwater"]:
        p = gpd.read_parquet(NET / f"{net}_pipes.parquet")
        p = p[p["Owner"] == "WCC"]
        f = diameter_factor(p["Diameter_mm"])
        is_local = p["Pipe_Type"].isin(PIPE_LOCAL_TYPES) & ~np.isnan(f)
        totals[f"{net}_local_km"] = p.loc[is_local].length.sum() / 1000
        totals[f"{net}_local_weighted_km"] = float((p.loc[is_local].length * f[is_local]).sum() / 1000)
        totals[f"{net}_shared_km"] = p.loc[~is_local].length.sum() / 1000
        print(net)
        out.append(attribute(sample_points(p[is_local], f[is_local]), parcels, f"{net}_local_w"))

    res = parcels[["pid", "wkb"]].set_index("pid").join(pd.concat(out, axis=1)).fillna(0)
    res.to_parquet(PROC / "parcel_frontage.parquet")
    (PROC / "network_totals.json").write_text(json.dumps(totals, indent=1))
    print(json.dumps({k: round(v, 1) for k, v in totals.items()}, indent=1))


if __name__ == "__main__":
    main()
