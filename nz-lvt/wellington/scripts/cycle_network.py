"""Build a routable cycling network for Wellington City from OpenStreetMap, with hills and traffic stress.

Edges: OSM ways cyclists can legally use (roads other than motorways, cycleways, and paths or
footways where bicycles are allowed), split wherever ways share a vertex.

Per edge:
  length_m            NZTM length
  up_fw, up_bw        metres climbed travelling in each direction, from the WCC 1 m LiDAR DEM
                      (2020) resampled to 5 m, sampled every 10 m; bridges and tunnels take a
                      straight line between their end elevations
  rise_abs            total absolute rise (for route hilliness)
  facility            separated (cycleway, cycle track, shared path designated for bikes),
                      sidepath (a road with a separated cycleway or path within 20 m along >= 70%
                      of it), painted (painted lane), bus (shared bus lane), none; from OSM tags
                      plus the WCC Strategic Bike Network 2022 "Current_facility" (matched where
                      its line runs within 25 m along >= 60% of the edge)
  adt, speed          traffic (WCC road layer, nearest centreline within 15 m) and speed limit
                      (OSM maxspeed; 50 km/h if untagged)
  lts                 level of traffic stress, simplified from Furth/Mekuria:
                        1  separated facility, path, living street or service lane
                        2  quiet street: <= 2,000 vehicles/day and <= 50 km/h (or a painted lane
                           on a street <= 30 km/h)
                        3  painted or bus lane on a busier street <= 50 km/h, or a street with
                           2,000-8,000 vehicles/day at <= 40 km/h
                        4  everything else (busy arterials, > 50 km/h)
  wcc_class, wcc_stage  the council's Strategic Bike Network class and stage, if on it
  name, highway, oneway

Writes data/processed/cycle_edges.parquet and data/processed/cycle_nodes.parquet.
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import tifffile
from pyproj import Transformer
from scipy.ndimage import map_coordinates
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "cycling"
NET = ROOT / "data" / "raw" / "networks"
PROC = ROOT / "data" / "processed"

ROADS = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
         "residential", "living_street", "service", "trunk", "trunk_link", "road"}
PATHS = {"cycleway", "path", "footway", "pedestrian", "track", "bridleway"}
BIKE_OK = {"yes", "designated", "permissive"}
SERVICE_SKIP = {"driveway", "parking_aisle", "drive-through", "emergency_access"}
DEM_EXTENT, DEM_RES = (1742080, 5418960, 1756480, 5443440), 5


def usable(t):
    h = t.get("highway")
    if t.get("bicycle") in ("no", "dismount", "private") or t.get("access") in ("no", "private"):
        return False
    if h in ROADS:
        return not (h == "service" and t.get("service") in SERVICE_SKIP)
    if h == "cycleway":
        return True
    if h in ("path", "track", "bridleway"):
        # Unsigned paths and tracks are mostly town belt walking and MTB trails: keep only if bikes
        # are signed, or if the path is paved.
        return t.get("bicycle") in BIKE_OK or t.get("surface") in ("asphalt", "concrete", "paved", "paving_stones")
    if h in ("footway", "pedestrian"):
        return t.get("bicycle") in BIKE_OK
    return False


def facility(t):
    h = t.get("highway")
    if h == "cycleway" or (h in PATHS and t.get("bicycle") == "designated"):
        return "separated"
    tags = [t.get(k, "") for k in ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both")]
    if any(v in ("track", "separate") for v in tags):
        return "separated"
    if any(v == "lane" for v in tags):
        return "painted"
    if any(v == "share_busway" for v in tags):
        return "bus"
    if h in PATHS:
        return "path"
    return "none"


def speed(t):
    try:
        return float(str(t.get("maxspeed", "50")).split()[0])
    except ValueError:
        return 50.0


def load_ways():
    els = json.loads((RAW / "osm_ways.json").read_text())["elements"]
    tf = Transformer.from_crs(4326, 2193, always_xy=True)
    ways = []
    for e in els:
        t = e.get("tags", {})
        if not usable(t) or len(e.get("geometry", [])) < 2:
            continue
        lon = [p["lon"] for p in e["geometry"]]
        lat = [p["lat"] for p in e["geometry"]]
        x, y = tf.transform(lon, lat)
        key = [(round(a, 7), round(b, 7)) for a, b in zip(lon, lat)]
        ways.append(dict(id=e["id"], tags=t, xy=np.column_stack([x, y]), key=key))
    return ways


def split(ways):
    """Split ways at shared vertices; return edge records and node coordinates."""
    count = {}
    for w in ways:
        for i, k in enumerate(w["key"]):
            count[k] = count.get(k, 0) + (2 if i in (0, len(w["key"]) - 1) else 1)
    node_id, nodes = {}, []

    def nid(k, xy):
        if k not in node_id:
            node_id[k] = len(nodes)
            nodes.append(xy)
        return node_id[k]

    edges = []
    for w in ways:
        t = w["tags"]
        cut = [0] + [i for i in range(1, len(w["key"]) - 1) if count[w["key"][i]] > 1] + [len(w["key"]) - 1]
        for a, b in zip(cut[:-1], cut[1:]):
            xy = w["xy"][a:b + 1]
            edges.append(dict(u=nid(w["key"][a], xy[0]), v=nid(w["key"][b], xy[-1]), geometry=LineString(xy),
                              osm_id=w["id"], highway=t.get("highway"), name=t.get("name"),
                              oneway=t.get("oneway") in ("yes", "1") and t.get("oneway:bicycle") != "no"
                              and t.get("cycleway") not in ("opposite", "opposite_lane"),
                              facility=facility(t), speed=speed(t),
                              structure="bridge" if t.get("bridge") not in (None, "no") else
                              "tunnel" if t.get("tunnel") not in (None, "no") else None))
    g = gpd.GeoDataFrame(edges, geometry="geometry", crs=2193)
    g = g[g.geometry.length > 0.5].reset_index(drop=True)
    return g, np.array(nodes)


def load_dem():
    xmin, ymin, xmax, ymax = DEM_EXTENT
    lower, upper = tifffile.imread(RAW / "dem_5m_0.tif"), tifffile.imread(RAW / "dem_5m_1.tif")
    z = np.vstack([upper, lower]).astype(float)  # row 0 = north
    z[(z < -50) | (z > 2000)] = np.nan
    return z


def sample(z, x, y):
    xmin, ymin, xmax, ymax = DEM_EXTENT
    col = (np.asarray(x) - xmin) / DEM_RES - 0.5
    row = (ymax - np.asarray(y)) / DEM_RES - 0.5
    return map_coordinates(z, [row, col], order=1, mode="constant", cval=np.nan)


def elevations(g, z):
    up_fw, up_bw, rise = [], [], []
    for geom, struct in zip(g.geometry, g["structure"]):
        n = max(2, int(geom.length // 10) + 1)
        pts = [geom.interpolate(d) for d in np.linspace(0, geom.length, n)]
        h = sample(z, [p.x for p in pts], [p.y for p in pts])
        if np.isnan(h).all():
            up_fw.append(0.0), up_bw.append(0.0), rise.append(0.0)
            continue
        h = pd.Series(h).interpolate(limit_direction="both").values
        if struct:  # deck or tunnel: straight grade between the ends
            h = np.linspace(h[0], h[-1], n)
        d = np.diff(h)
        up_fw.append(d[d > 0].sum()), up_bw.append(-d[d < 0].sum()), rise.append(np.abs(d).sum())
    g["up_fw"], g["up_bw"], g["rise_abs"] = up_fw, up_bw, rise
    return g


def nearest_attr(g, other, cols, max_dist):
    mid = gpd.GeoDataFrame(geometry=g.geometry.interpolate(0.5, normalized=True), index=g.index, crs=2193)
    j = gpd.sjoin_nearest(mid, other[cols + ["geometry"]], how="left", max_distance=max_dist)
    return j[~j.index.duplicated()][cols].reindex(g.index)


def overlap_attr(g, other, cols, buf):
    """Attributes of the `other` line that runs along most of each edge (>= 60% within buf m)."""
    ob = other[cols + ["geometry"]].copy()
    ob["geometry"] = ob.buffer(buf, cap_style="flat")
    ob["oid"] = np.arange(len(ob))
    j = gpd.sjoin(g[["geometry"]], ob, predicate="intersects")
    j["share"] = [g.geometry.loc[i].intersection(ob.geometry.iloc[o]).length / max(g.geometry.loc[i].length, 0.1)
                  for i, o in zip(j.index, j["oid"])]
    j = j[j["share"] >= 0.6].sort_values("share", ascending=False)
    j = j[~j.index.duplicated()]
    return j[cols].reindex(g.index)


def sidepaths(g, buf=20):
    """Busy-road edges with a separated cycleway or shared path alongside count as protected."""
    prot = g[g["facility"].isin(["separated", "path"]) & ~g["highway"].isin(ROADS)]
    zone = prot.buffer(buf, cap_style="flat").union_all()
    road = g["highway"].isin(ROADS) & ~g["facility"].isin(["separated"]) & ~g["highway"].isin(
        ["service", "living_street"])
    share = g.loc[road].geometry.intersection(zone).length / g.loc[road, "length_m"].clip(lower=0.1)
    side = share[share >= 0.7].index
    g.loc[side, "facility"] = "sidepath"
    print(f"roads with a parallel path or cycleway: {g.loc[side, 'length_m'].sum() / 1000:,.0f} km")
    return g


def lts(r):
    if r["facility"] in ("separated", "path", "sidepath") or r["highway"] in ("living_street", "service", "cycleway"):
        return 1
    adt = r["adt"] if r["adt"] == r["adt"] else (800 if r["highway"] in ("residential", "unclassified") else 6000)
    sp = r["speed"]
    if sp > 50:
        return 4
    if (adt <= 2000 and sp <= 50) or (r["facility"] == "painted" and sp <= 30):
        return 2
    if r["facility"] in ("painted", "bus") or (adt <= 8000 and sp <= 40):
        return 3
    return 4


def main():
    ways = load_ways()
    g, nodes = split(ways)
    print(f"{len(ways):,} usable ways -> {len(g):,} edges, {len(nodes):,} nodes, "
          f"{g.geometry.length.sum() / 1000:,.0f} km")
    g = elevations(g, load_dem())

    roads = gpd.read_parquet(NET / "roads.parquet")[["adt", "category", "geometry"]]
    roads["adt"] = pd.to_numeric(roads["adt"], errors="coerce")
    car = g["highway"].isin(ROADS)
    g[["adt", "wcc_road_cat"]] = np.nan, None
    g.loc[car, ["adt", "wcc_road_cat"]] = nearest_attr(g[car], roads.rename(columns={"category": "wcc_road_cat"}),
                                                       ["adt", "wcc_road_cat"], 15).values
    bike = gpd.read_parquet(NET / "wcc_bike_network.parquet")
    b = overlap_attr(g, bike, ["Current_facility", "Network_class", "Network_stage"], 25)
    wcc_fac = b["Current_facility"].map({"Separated": "separated", "Barrier": "separated", "Painted": "painted"})
    upgrade = (g["facility"].isin(["none", "bus"]) & wcc_fac.notna()) | ((g["facility"] == "painted") &
                                                                          (wcc_fac == "separated"))
    g.loc[upgrade, "facility"] = wcc_fac[upgrade]
    g["wcc_class"], g["wcc_stage"] = b["Network_class"], b["Network_stage"]
    g["length_m"] = g.geometry.length
    g = sidepaths(g)
    g["lts"] = g.apply(lts, axis=1)
    print(g.groupby("lts")["length_m"].sum().div(1000).round(0).to_string())
    print(g.groupby("facility")["length_m"].sum().div(1000).round(0).to_string())
    g.drop(columns=["structure"]).to_parquet(PROC / "cycle_edges.parquet")
    pd.DataFrame(nodes, columns=["x", "y"]).to_parquet(PROC / "cycle_nodes.parquet")


if __name__ == "__main__":
    main()
