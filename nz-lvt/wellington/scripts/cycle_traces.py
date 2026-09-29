"""Where people actually ride: OpenStreetMap public GPS traces in Wellington City.

Downloads public trackpoints (api.openstreetmap.org/api/0.6/trackpoints, 5,000 points a page) for
the city bbox into data/raw/cycling/osm_traces/. Traces carry no mode, so each timed segment is
split into runs and kept as a likely ride when its median speed is 10-35 km/h and it never holds
over 45 km/h for more than 20 s (walks are slower, driving on arterials faster). Untimed segments
are dropped.

Kept rides are matched to the modelled network (nearest edge within 15 m, every point) and
counted as distinct trace-segments per edge. Writes:
  outputs/tables/cycle_traces_edges.csv      rides per edge, with model stress and Go Dutch flow
  outputs/blocks/cycle_traces.geojson         edges with rides (for the map), and
                                              off-network clusters: 25 m cells with ride points
                                              from 3+ traces more than 25 m from any street or path
Run with --fetch to (re)download. Analysis alone takes a minute; no model rerun needed.
"""

import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "cycling" / "osm_traces"
PROC = ROOT / "data" / "processed"
TABLES = ROOT / "outputs" / "tables"
BLOCKS = ROOT / "outputs" / "blocks"
BBOX = (174.61, -41.37, 174.90, -41.14)
API = "https://api.openstreetmap.org/api/0.6/trackpoints"
NS = {"g": "http://www.topografix.com/GPX/1/0"}


def fetch(max_pages=2000):
    RAW.mkdir(parents=True, exist_ok=True)
    page = len(list(RAW.glob("page_*.gpx")))
    while page < max_pages:
        # curl: the API rate-limits python-requests from this host but not curl.
        out = RAW / f"page_{page:05d}.gpx"
        for attempt in range(8):
            code = subprocess.run(["curl", "-s", "--max-time", "180", "-o", str(out), "-w", "%{http_code}",
                                   f"{API}?bbox={','.join(map(str, BBOX))}&page={page}"],
                                  capture_output=True, text=True).stdout
            if code == "200":
                break
            time.sleep(30 * (attempt + 1))
        else:
            raise RuntimeError(f"page {page} failed ({code})")
        text = out.read_text()
        n = text.count("<trkpt")
        if page % 50 == 0:
            print(f"page {page}: {n} points", flush=True)
        if n < 5000:
            break
        page += 1
        time.sleep(3)


def segments():
    """Yield (segment id, DataFrame lon, lat, t) for timed segments across all pages."""
    sid = 0
    for f in sorted(RAW.glob("page_*.gpx")):
        root = ET.parse(f).getroot()
        for seg in root.iter("{http://www.topografix.com/GPX/1/0}trkseg"):
            rows = []
            for p in seg.findall("g:trkpt", NS):
                t = p.find("g:time", NS)
                if t is None:
                    break
                rows.append((float(p.get("lon")), float(p.get("lat")), t.text))
            if len(rows) >= 10:
                sid += 1
                yield sid, pd.DataFrame(rows, columns=["lon", "lat", "t"])


def rides():
    tf = Transformer.from_crs(4326, 2193, always_xy=True)
    kept, n_all = [], 0
    for sid, d in segments():
        n_all += 1
        d["x"], d["y"] = tf.transform(d["lon"].values, d["lat"].values)
        d["t"] = pd.to_datetime(d["t"], utc=True, errors="coerce")
        d = d.dropna(subset=["t"]).sort_values("t")
        dt = d["t"].diff().dt.total_seconds().values
        ds = np.hypot(np.diff(d["x"].values, prepend=np.nan), np.diff(d["y"].values, prepend=np.nan))
        v = ds / np.where(dt > 0, dt, np.nan) * 3.6
        # Split at gaps over 60 s or 300 m.
        brk = (dt > 60) | (ds > 300)
        d["run"] = np.cumsum(np.nan_to_num(brk, nan=1).astype(bool))
        d["v"] = v
        for _, r in d.groupby("run"):
            if len(r) < 10 or r["t"].iloc[-1] - r["t"].iloc[0] < pd.Timedelta(seconds=60):
                continue
            med = np.nanmedian(r["v"])
            fast = (r["v"] > 45).rolling(20, min_periods=20).sum().max() if len(r) >= 20 else 0
            if 10 <= med <= 35 and not (fast and fast >= 20):
                kept.append(r.assign(seg=f"{sid}_{r['run'].iat[0]}")[["seg", "x", "y", "v"]])
    print(f"timed segments: {n_all:,}; likely rides: {len(kept):,}")
    return pd.concat(kept, ignore_index=True)


def main():
    if "--fetch" in sys.argv or not any(RAW.glob("page_*.gpx")):
        fetch()
    pts = rides()
    e = gpd.read_parquet(PROC / "cycle_edges.parquet").join(pd.read_parquet(PROC / "cycle_flows.parquet"))
    g = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts["x"], pts["y"]), crs=2193)
    j = gpd.sjoin_nearest(g, e[["geometry"]], how="left", max_distance=15, distance_col="d")
    j = j[~j.index.duplicated()]
    on = j[j["index_right"].notna()]
    per = on.groupby("index_right")["seg"].nunique().rename("rides")
    ee = e.loc[per.index.astype(int)].copy()
    ee["rides"] = per.values
    cols = ["name", "highway", "facility", "lts", "lts_fw", "lts_bw", "adt", "speed", "flow_census", "flow_godutch",
            "rides", "length_m"]
    ee[cols].sort_values("rides", ascending=False).round(1).to_csv(TABLES / "cycle_traces_edges.csv")
    # Off-network clusters.
    off = j[j["index_right"].isna()].copy()
    off["cx"], off["cy"] = (off["x"] // 25).astype(int), (off["y"] // 25).astype(int)
    c = off.groupby(["cx", "cy"]).agg(x=("x", "mean"), y=("y", "mean"), traces=("seg", "nunique"),
                                      points=("seg", "size")).reset_index()
    c = c[c["traces"] >= 3]
    near = gpd.sjoin_nearest(gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c["x"], c["y"]), crs=2193),
                             e[["geometry"]], distance_col="d")
    c = near[~near.index.duplicated() & (near["d"] > 25)]
    print(f"edges with rides: {len(ee):,}; off-network cells: {len(c):,}")
    # Busy streets people ride.
    busy = ee[ee["lts"] >= 3].sort_values("rides", ascending=False)
    print("busiest-ridden stress 3-4 streets:", busy.groupby("name")["rides"].max().sort_values(ascending=False)
          .head(15).to_dict())
    out_e = ee[["geometry", "name", "rides", "lts", "facility", "flow_godutch"]].assign(kind="edge")
    out_e["geometry"] = out_e.geometry.simplify(2)
    out_c = gpd.GeoDataFrame(c[["traces"]].rename(columns={"traces": "rides"}).assign(kind="off_network"),
                             geometry=c.geometry, crs=2193)
    out = pd.concat([out_e, out_c], ignore_index=True)
    gpd.GeoDataFrame(out, geometry="geometry", crs=2193).to_crs(4326).to_file(
        BLOCKS / "cycle_traces.geojson", driver="GeoJSON")
    # Agreement with the model.
    from scipy.stats import spearmanr
    m = ee[ee["length_m"] >= 30]
    print(f"rank correlation rides vs modelled census flow: {spearmanr(m['rides'], m['flow_census']).statistic:.2f}")


if __name__ == "__main__":
    main()
