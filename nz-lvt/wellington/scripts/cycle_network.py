"""Build a routable, direction-aware cycling network for Wellington City from OpenStreetMap.

Edges: OSM ways cyclists can legally use (roads other than motorways, cycleways, and paths or
footways where bicycles are allowed; ways closed to general traffic but open to bikes are kept),
split wherever ways share a vertex. "fw" is the direction the OSM way is drawn, "bw" the reverse.

Per edge:
  length_m, up_fw, up_bw, rise_abs
      length; metres climbed each way and total absolute rise, from the WCC 1 m LiDAR DEM (2020)
      resampled to 5 m and smoothed (Gaussian, sigma 10 m) so kerbs and noise don't read as
      hills; sampled every 10 m. Bridges and tunnels take a straight grade between their ends.
  can_fw, can_bw
      whether a bike may ride each way (oneway, oneway:bicycle=no, opposite_* contraflow).
  fac_fw, fac_bw, facility
      facility level each way, and the best of the two:
        track            cycleway not shared with walkers (foot=no), or cycleway:<side>=track
        protected_lane   painted lane with physical separation (cycleway:<side>:separation, e.g.
                         the flexible posts of WCC's transitional programme), or WCC "Barrier"
        sidepath         road with a separately mapped cycleway or path alongside (<= 20 m, >= 70%
                         of its length), or cycleway:<side>=separate
        seg_path         path with segregated=yes
        shared_path      cycleway or path shared with walkers (the default for untagged cycleways:
                         most NZ cycleways are legally shared paths)
        shared_footway   footway or pedestrian way where bikes are allowed, or a paved path
        buffered_lane    painted lane with a marked buffer
        painted_lane     painted lane (or shoulder)
        bus_lane         shared bus lane
        sharrow          painted bike symbols in a general traffic lane (no protection)
        mixed            nothing
      NZ drives on the left, so on two-way roads cycleway:left applies to fw and cycleway:right to bw.
  speed, speed_src
      legal limit: NZTA National Speed Limit Register (the smallest permanent zone containing the
      edge midpoint, so a 30 km/h area beats the city-wide 50), else OSM maxspeed, else 50.
  adt, adt_src
      vehicles/day: WCC road layer (nearest centreline within 15 m), else NZTA state highway
      monitoring site (latest AADT, nearest within 1 km, trunk roads only), else a default by road
      class (osmactive): residential/unclassified/service/living 500, tertiary 3,000,
      secondary 5,000, primary 6,000, trunk 8,000.
  lanes_dir, centre_line
      general traffic lanes per direction (OSM lanes, else 1) and whether the road has a centre
      line (tertiary and above, or 2+ lanes).
  lts_fw, lts_bw, lts
      level of traffic stress each way (max over the allowed directions in `lts`), from the table
      in stress() below: Furth LTS v2 (2017) for mixed traffic by speed x volume x centre line,
      adjusted with UK LTN 1/20 Fig 4.1, the CROW design manual and Auckland Transport's cycling
      design code (30 km/h streets up to ~3,000 vehicles/day are fine to share; 3,000-5,000 is
      level 2 without a centre line). One-way streets count at 1.5 x ADT. Downhill at > 4% on
      streets <= 50 km/h, mixed traffic is rated one speed band lower (riders descend near traffic
      speed), but no better than level 2. Protected facilities are level 1, except shared paths,
      sidepaths and footways beside a road of >= 70 km/h or >= 20,000 vehicles/day (e.g. Aotea
      Quay, Hutt Road by SH1): level 2 (side_speed, side_adt, roadside_fw/bw; see roadside()).
  cross_u, cross_v
      stress of crossing at each end node: at an unsignalised junction, the highest LTS of the
      other roads meeting there (Conveyal's rule); 2 where signals are within 25 m, or where a
      zebra/marked crossing is within 20 m of a road <= 50 km/h and <= 8,000 vehicles/day; 1 at
      non-junctions.
  wcc_class, wcc_stage, wcc_facility, name, highway, osm_id

Writes data/processed/cycle_edges.parquet and data/processed/cycle_nodes.parquet.
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import tifffile
from pyproj import Transformer
from scipy.ndimage import gaussian_filter, map_coordinates
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "cycling"
NET = ROOT / "data" / "raw" / "networks"
PROC = ROOT / "data" / "processed"

ROADS = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
         "residential", "living_street", "service", "trunk", "trunk_link", "road"}
MAJOR = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "trunk",
         "trunk_link"}
PATHS = {"cycleway", "path", "footway", "pedestrian", "track", "bridleway"}
BIKE_OK = {"yes", "designated", "permissive"}
SERVICE_SKIP = {"driveway", "parking_aisle", "drive-through", "emergency_access"}
DEM_EXTENT, DEM_RES = (1742080, 5418960, 1756480, 5443440), 5
PROTECTED = {"track", "protected_lane", "sidepath", "seg_path", "shared_path", "shared_footway"}
# Shared paths beside roads this fast or busy rate level 2, not 1 (roadside()); kerbed tracks and
# protected lanes keep level 1.
ROADSIDE_FAC = {"sidepath", "seg_path", "shared_path", "shared_footway"}
ROADSIDE_SPEED, ROADSIDE_ADT = 70, 20000
RANK = ["track", "protected_lane", "sidepath", "seg_path", "shared_path", "shared_footway", "buffered_lane",
        "painted_lane", "bus_lane", "sharrow", "mixed"]
DEFAULT_ADT = {"residential": 500, "unclassified": 500, "service": 500, "living_street": 200, "road": 500,
               "tertiary": 3000, "tertiary_link": 3000, "secondary": 5000, "secondary_link": 5000,
               "primary": 6000, "primary_link": 6000, "trunk": 8000, "trunk_link": 8000}


def usable(t):
    h = t.get("highway")
    if t.get("bicycle") in ("no", "dismount", "private"):
        return False
    # access=no with bicycle=yes/designated is a bike-and-pedestrian (or bus-and-bike) way.
    if t.get("access") in ("no", "private") and t.get("bicycle") not in BIKE_OK:
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


def path_level(t):
    h = t.get("highway")
    if h == "cycleway" and t.get("foot") == "no" and t.get("footway") != "sidewalk":
        return "track"
    if t.get("segregated") == "yes":
        return "seg_path"
    if h == "cycleway" or t.get("bicycle") == "designated":
        return "shared_path"
    return "shared_footway"


def side_level(t, side):
    """Facility level from the tags for one side of a road."""
    v = t.get(f"cycleway:{side}") or t.get("cycleway:both") or t.get("cycleway") or "no"
    pre = f"cycleway:{side}" if t.get(f"cycleway:{side}") else "cycleway:both" if t.get("cycleway:both") else "cycleway"
    v = v.replace("opposite_", "") if v.startswith("opposite_") else v
    sep = t.get(f"{pre}:separation") or t.get(f"cycleway:{side}:separation") or t.get("cycleway:both:separation")
    buf = t.get(f"{pre}:buffer") or t.get(f"cycleway:{side}:buffer")
    if v == "track":
        return "shared_path" if t.get(f"{pre}:segregated") == "no" else "track"
    if v == "separate":
        return "sidepath"
    if v in ("lane", "buffered_lane", "shoulder"):
        if sep and sep not in ("no", "none"):
            return "protected_lane"
        if v == "buffered_lane" or (buf and buf != "no"):
            return "buffered_lane"
        return "painted_lane"
    if v == "share_busway":
        return "bus_lane"
    if v == "shared_lane":
        return "sharrow"
    return "mixed"


def directions(t):
    """(can_fw, can_bw, fac_fw, fac_bw) for a way."""
    h = t.get("highway")
    ow = t.get("oneway")
    contra = t.get("oneway:bicycle") == "no" or any(
        str(t.get(k, "")).startswith("opposite") for k in ("cycleway", "cycleway:left", "cycleway:right",
                                                           "cycleway:both"))
    if h in PATHS:
        lvl = path_level(t)
        one = ow in ("yes", "1") and t.get("oneway:bicycle") != "no"
        return True, not one, lvl, lvl
    left, right = side_level(t, "left"), side_level(t, "right")
    if ow in ("yes", "1", "-1"):
        # Both sides serve the traffic direction; a contraflow facility is tagged opposite_* or on
        # the side tagged cycleway:<side>:oneway=-1.
        with_flow = [s for s, v in (("left", left), ("right", right))
                     if t.get(f"cycleway:{s}:oneway") != "-1" and not str(t.get(f"cycleway:{s}", "")).startswith("opposite")]
        against = [s for s in ("left", "right") if s not in with_flow]
        best = lambda sides, lv: min((lv[s] for s in sides), key=RANK.index, default="mixed")
        lv = {"left": left, "right": right}
        fac_with = best(with_flow, lv) if with_flow else "mixed"
        fac_against = best(against, lv) if against else "mixed"
        if str(t.get("cycleway", "")).startswith("opposite"):
            fac_against = side_level({"cycleway": t["cycleway"]}, "both")
        if ow == "-1":
            return contra, True, fac_against, fac_with
        return True, contra, fac_with, fac_against
    return True, True, left, right


def speed_tag(t):
    try:
        return float(str(t.get("maxspeed")).split()[0])
    except ValueError:
        return np.nan


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
        can_fw, can_bw, fac_fw, fac_bw = directions(t)
        try:
            lanes = int(str(t.get("lanes", "")).split(";")[0])
        except ValueError:
            lanes = None
        one_way = not (can_fw and can_bw) or t.get("oneway") in ("yes", "1", "-1")
        lanes_dir = max(1, lanes if (lanes and one_way) else (lanes // 2 if lanes else 1))
        cut = [0] + [i for i in range(1, len(w["key"]) - 1) if count[w["key"][i]] > 1] + [len(w["key"]) - 1]
        for a, b in zip(cut[:-1], cut[1:]):
            xy = w["xy"][a:b + 1]
            edges.append(dict(u=nid(w["key"][a], xy[0]), v=nid(w["key"][b], xy[-1]), geometry=LineString(xy),
                              osm_id=w["id"], highway=t.get("highway"), name=t.get("name"),
                              can_fw=can_fw, can_bw=can_bw, fac_fw=fac_fw, fac_bw=fac_bw,
                              motor_oneway=t.get("oneway") in ("yes", "1", "-1"),
                              osm_speed=speed_tag(t), lanes_dir=lanes_dir,
                              centre_line=t.get("highway") in MAJOR or (lanes or 0) >= 2,
                              structure="bridge" if t.get("bridge") not in (None, "no") else
                              "tunnel" if t.get("tunnel") not in (None, "no") else None))
    g = gpd.GeoDataFrame(edges, geometry="geometry", crs=2193)
    g = g[g.geometry.length > 0.5].reset_index(drop=True)
    return g, np.array(nodes)


def load_dem(sigma_m=10):
    lower, upper = tifffile.imread(RAW / "dem_5m_0.tif"), tifffile.imread(RAW / "dem_5m_1.tif")
    z = np.vstack([upper, lower]).astype(float)  # row 0 = north
    z[(z < -50) | (z > 2000)] = np.nan
    if sigma_m:
        ok = np.isfinite(z)
        s = sigma_m / DEM_RES
        num = gaussian_filter(np.where(ok, z, 0), s)
        den = gaussian_filter(ok.astype(float), s)
        z = np.where(ok, num / np.maximum(den, 1e-6), np.nan)
    return z


def sample(z, x, y):
    xmin, ymin, xmax, ymax = DEM_EXTENT
    col = (np.asarray(x) - xmin) / DEM_RES - 0.5
    row = (ymax - np.asarray(y)) / DEM_RES - 0.5
    return map_coordinates(z, [row, col], order=1, mode="constant", cval=np.nan)


def elevations(g, z):
    up_fw, up_bw, rise, z0, z1 = [], [], [], [], []
    for geom, struct in zip(g.geometry, g["structure"]):
        n = max(2, int(geom.length // 10) + 1)
        pts = [geom.interpolate(d) for d in np.linspace(0, geom.length, n)]
        h = sample(z, [p.x for p in pts], [p.y for p in pts])
        if np.isnan(h).all():
            up_fw.append(0.0), up_bw.append(0.0), rise.append(0.0), z0.append(np.nan), z1.append(np.nan)
            continue
        h = pd.Series(h).interpolate(limit_direction="both").values
        if struct:  # deck or tunnel: straight grade between the ends
            h = np.linspace(h[0], h[-1], n)
        d = np.diff(h)
        up_fw.append(d[d > 0].sum()), up_bw.append(-d[d < 0].sum()), rise.append(np.abs(d).sum())
        z0.append(h[0]), z1.append(h[-1])
    g["up_fw"], g["up_bw"], g["rise_abs"], g["z_u"], g["z_v"] = up_fw, up_bw, rise, z0, z1
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
    """Road edges with a separated cycleway or shared path alongside count as protected both ways."""
    prot = g[g["fac_fw"].isin(["track", "seg_path", "shared_path"]) & ~g["highway"].isin(ROADS)]
    zone = prot.buffer(buf, cap_style="flat").union_all()
    road = g["highway"].isin(ROADS) & ~g["highway"].isin(["service", "living_street"])
    share = g.loc[road].geometry.intersection(zone).length / g.loc[road, "length_m"].clip(lower=0.1)
    side = share[share >= 0.7].index
    for c in ("fac_fw", "fac_bw"):
        better = g.loc[side, c].map(RANK.index) > RANK.index("sidepath")
        g.loc[side[better.values], c] = "sidepath"
    print(f"roads with a parallel path or cycleway: {g.loc[side, 'length_m'].sum() / 1000:,.0f} km")
    return g


def roadside(g, buf=20):
    """Speed and traffic of the busiest major road alongside each protected edge.

    A shared path beside a 30,000-vehicle quay or an 80-100 km/h highway (Aotea Quay, Hutt Road
    by SH1/SH2) is separated from traffic but noisy and exposed, "a footpath next to a motorway".
    Road edges carry their own speed and volume; paths take the busiest major road whose 20 m
    corridor covers at least half their length."""
    g["side_speed"] = np.where(g["highway"].isin(ROADS), g["speed"], np.nan)
    g["side_adt"] = np.where(g["highway"].isin(ROADS), g["adt"], np.nan)
    path = ~g["highway"].isin(ROADS) & (g["fac_fw"].isin(PROTECTED) | g["fac_bw"].isin(PROTECTED))
    rd = g[g["highway"].isin(MAJOR)][["speed", "adt", "geometry"]].copy()
    rd["geometry"] = rd.buffer(buf, cap_style="flat")
    rd["rid"] = np.arange(len(rd))
    j = gpd.sjoin(g.loc[path, ["geometry"]], rd, predicate="intersects")
    j["share"] = [g.geometry.loc[i].intersection(rd.geometry.iloc[r]).length / max(g.at[i, "length_m"], 0.1)
                  for i, r in zip(j.index, j["rid"])]
    j = j[j["share"] >= 0.5]
    agg = j.groupby(level=0).agg(s=("speed", "max"), a=("adt", "max"))
    g.loc[agg.index, "side_speed"], g.loc[agg.index, "side_adt"] = agg["s"], agg["a"]
    busy = (g["side_speed"] >= ROADSIDE_SPEED) | (g["side_adt"] >= ROADSIDE_ADT)
    for d in ("fw", "bw"):
        g[f"roadside_{d}"] = busy & g[f"fac_{d}"].isin(ROADSIDE_FAC)
    print(f"protected facilities beside busy roads: {g.loc[g['roadside_fw'] | g['roadside_bw'], 'length_m'].sum() / 1000:,.1f} km")
    return g


def speeds(g):
    z = gpd.read_file(RAW / "speed_zones.geojson")
    z = z[z["speedCategoryName"] == "Permanent"].set_crs(2193, allow_override=True)
    # Some register polygons are invalid (self-touching corridor rings), which breaks spatial joins.
    bad = ~z.geometry.is_valid
    z.loc[bad, "geometry"] = z.loc[bad, "geometry"].buffer(0)
    z["limit"] = pd.to_numeric(z["speedLimitZoneValue"], errors="coerce")
    z["area"] = z.geometry.area
    mid = gpd.GeoDataFrame(geometry=g.geometry.interpolate(0.5, normalized=True), index=g.index, crs=2193)
    # Zones containing the midpoint first (smallest wins: a 30 km/h area inside the city-wide 50);
    # only if none, zones within 3 m (centrelines can sit just outside a corridor polygon).
    j = gpd.sjoin(mid, z[["limit", "area", "geometry"]], predicate="within").sort_values("area")
    j = j[~j.index.duplicated()]
    rest = mid.loc[mid.index.difference(j.index)].copy()
    rest["geometry"] = rest.buffer(3)
    j2 = gpd.sjoin(rest, z[["limit", "area", "geometry"]], predicate="intersects").sort_values("area")
    j = pd.concat([j, j2[~j2.index.duplicated()]])
    g["speed"] = j["limit"].reindex(g.index)
    g["speed_src"] = np.where(g["speed"].notna(), "NSLR", None)
    osm = g["speed"].isna() & g["osm_speed"].notna()
    g.loc[osm, "speed"], g.loc[osm, "speed_src"] = g.loc[osm, "osm_speed"], "OSM"
    g.loc[g["speed"].isna(), "speed_src"] = "default"
    g["speed"] = g["speed"].fillna(50)
    road = g["highway"].isin(ROADS)
    print("speed source (road km):", g[road].groupby("speed_src")["length_m"].sum().div(1000).round(0).to_dict())
    changed = road & g["osm_speed"].notna() & (g["osm_speed"] != g["speed"])
    print(f"  NSLR differs from OSM maxspeed on {g.loc[changed, 'length_m'].sum() / 1000:,.0f} km")
    return g


def volumes(g):
    roads = gpd.read_parquet(NET / "roads.parquet")[["adt", "category", "geometry"]]
    roads["adt"] = pd.to_numeric(roads["adt"], errors="coerce")
    car = g["highway"].isin(ROADS)
    g["adt"], g["adt_src"] = np.nan, None
    g.loc[car, "adt"] = nearest_attr(g[car], roads, ["adt"], 15)["adt"].values
    g.loc[g["adt"].notna(), "adt_src"] = "WCC"
    sh = gpd.read_file(RAW / "sh_sites.geojson").set_crs(2193, allow_override=True)
    sh["aadt"] = sh[["aadt1yearago", "aadt2yearsago", "aadt3yearsago", "aadt4yearsago", "aadt5yearsago"]].bfill(
        axis=1).iloc[:, 0]
    sh = sh[sh["aadt"].notna()]
    trunk = g["highway"].isin(["trunk", "trunk_link"]) & g["adt"].isna()
    g.loc[trunk, "adt"] = nearest_attr(g[trunk], sh, ["aadt"], 1000)["aadt"].values
    g.loc[trunk & g["adt"].notna(), "adt_src"] = "NZTA"
    miss = car & g["adt"].isna()
    g.loc[miss, "adt"] = g.loc[miss, "highway"].map(DEFAULT_ADT).fillna(500)
    g.loc[miss, "adt_src"] = "default"
    print("ADT source (road km):", g[car].groupby("adt_src")["length_m"].sum().div(1000).round(0).to_dict())
    return g


def band(speed):
    return 0 if speed <= 30 else 1 if speed <= 40 else 2 if speed <= 50 else 3 if speed <= 60 else 4


# Mixed traffic, one lane each way. Rows: speed band (<=30, 40, 50, 60, >=70); columns: ADT
# <=750, <=1,500, <=3,000, <=5,000, <=8,000, >8,000.
MIXED_NO_CENTRE = [[1, 1, 2, 2, 3, 3], [1, 1, 2, 3, 3, 4], [2, 2, 2, 3, 3, 4], [2, 3, 3, 3, 4, 4],
                   [3, 3, 4, 4, 4, 4]]
MIXED_CENTRE = [[1, 2, 2, 3, 3, 3], [1, 2, 3, 3, 3, 4], [2, 2, 3, 3, 3, 4], [2, 3, 3, 3, 4, 4],
                [3, 3, 4, 4, 4, 4]]
ADT_BANDS = [750, 1500, 3000, 5000, 8000]


def stress(fac, speed, adt, lanes_dir, centre, one_way, highway, downhill):
    if fac in PROTECTED or highway in ("living_street", "service", "cycleway", "pedestrian"):
        return 1
    b = band(speed)
    eff = adt * (1.5 if one_way else 1.0)
    if fac in ("painted_lane", "buffered_lane", "bus_lane"):
        if b <= 1 and lanes_dir == 1:
            lts = 1 if fac == "buffered_lane" else 2
        elif b == 2:
            lts = 3 if (lanes_dir >= 3 or (fac == "painted_lane" and eff > 6000) or eff > 10000) else 2
        elif b == 3:
            lts = 3
        elif b >= 4:
            lts = 4
        else:
            lts = 3
        if fac == "bus_lane" and b >= 2:
            lts = max(lts, 3)
        return lts
    # Mixed traffic and sharrows.
    if downhill and b in (1, 2):
        b -= 1
    if lanes_dir >= 2:
        lts = 3 if b <= 1 else (3 if (b == 2 and eff <= 8000) else 4)
    else:
        col = int(np.searchsorted(ADT_BANDS, eff, side="left"))
        lts = (MIXED_CENTRE if centre else MIXED_NO_CENTRE)[b][col]
    return max(lts, 2) if downhill else lts


def junctions(g, nodes):
    """Crossing stress at each node, stored on edges as cross_u / cross_v."""
    tf = Transformer.from_crs(4326, 2193, always_xy=True)
    els = json.loads((RAW / "osm_nodes.json").read_text())["elements"]
    sig, marked = [], []
    for e in els:
        t = e.get("tags", {})
        x, y = tf.transform(e["lon"], e["lat"])
        if "traffic_signals" in str(t.get("highway", "")) or t.get("crossing") == "traffic_signals":
            sig.append((x, y))
        elif t.get("crossing") in ("zebra", "marked", "uncontrolled") or t.get("crossing_ref") == "zebra":
            marked.append((x, y))
    from scipy.spatial import cKDTree
    xy = nodes
    near_sig = cKDTree(np.array(sig)).query(xy, distance_upper_bound=25)[0] < 25
    near_mark = cKDTree(np.array(marked)).query(xy, distance_upper_bound=20)[0] < 20
    road = g[g["highway"].isin(ROADS - {"service", "living_street"})]
    inc = pd.concat([pd.DataFrame({"node": road["u"], "edge": road.index}),
                     pd.DataFrame({"node": road["v"], "edge": road.index})])
    inc = inc.join(road[["lts", "name", "speed", "adt"]], on="edge")
    deg = pd.concat([g["u"], g["v"]]).value_counts()
    cross = {}
    for end in ("u", "v"):
        own = g[[end, "name"]].rename(columns={end: "node"}).reset_index().rename(columns={"index": "edge"})
        m = own.merge(inc, on="node", suffixes=("", "_x"))
        m = m[(m["edge"] != m["edge_x"]) & ((m["name_x"] != m["name"]) | m["name"].isna())]
        mx = m.groupby("edge").agg(lts=("lts", "max"), speed=("speed", "max"), adt=("adt", "max"))
        s = pd.Series(1, index=g.index, dtype=float)
        s.loc[mx.index] = mx["lts"]
        node = g[end]
        junction = node.map(deg).fillna(0).values >= 3
        s[~junction] = 1
        s[near_sig[node.values] & (s > 2)] = 2
        mk = near_mark[node.values] & (s > 2)
        ok = mx.reindex(g.index)
        mk &= (ok["speed"].fillna(50) <= 50).values & (ok["adt"].fillna(0) <= 8000).values
        s[mk] = 2
        cross[end] = s.astype(int)
    g["cross_u"], g["cross_v"] = cross["u"], cross["v"]
    print(f"signalised nodes: {near_sig.sum():,}; edges ending at a high-stress unsignalised crossing: "
          f"{((g['cross_u'] >= 3) | (g['cross_v'] >= 3)).sum():,}")
    return g


def main():
    ways = load_ways()
    g, nodes = split(ways)
    print(f"{len(ways):,} usable ways -> {len(g):,} edges, {len(nodes):,} nodes, "
          f"{g.geometry.length.sum() / 1000:,.0f} km")
    g = elevations(g, load_dem())
    g["length_m"] = g.geometry.length
    g = speeds(g)
    g = volumes(g)

    bike = gpd.read_parquet(NET / "wcc_bike_network.parquet")
    b = overlap_attr(g, bike, ["Current_facility", "Network_class", "Network_stage"], 25)
    g["wcc_facility"], g["wcc_class"], g["wcc_stage"] = b["Current_facility"], b["Network_class"], b["Network_stage"]
    # Where OSM shows nothing on either side but the council layer records protection, apply it both
    # ways and flag it (the council layer has no direction).
    wcc_fac = b["Current_facility"].map({"Separated": "protected_lane", "Barrier": "protected_lane",
                                         "Painted": "painted_lane"})
    blank = g["fac_fw"].isin(["mixed", "sharrow"]) & g["fac_bw"].isin(["mixed", "sharrow"]) & wcc_fac.notna()
    for c in ("fac_fw", "fac_bw"):
        g.loc[blank, c] = wcc_fac[blank]
    g["fac_from_wcc"] = blank
    g = sidepaths(g)
    g["facility"] = [min(a, b, key=RANK.index) for a, b in zip(g["fac_fw"], g["fac_bw"])]

    L = g["length_m"].clip(lower=1)
    grade_fw = (g["z_v"] - g["z_u"]) / L  # + = uphill travelling fw
    oneway_motor = g["motor_oneway"]
    for d, sign in (("fw", 1), ("bw", -1)):
        down = (sign * grade_fw < -0.04) & (g["speed"] <= 50)
        g[f"lts_{d}"] = [stress(f, s, a, ln, c, o, h, dn) for f, s, a, ln, c, o, h, dn in zip(
            g[f"fac_{d}"], g["speed"], g["adt"].fillna(0), g["lanes_dir"], g["centre_line"], oneway_motor,
            g["highway"], down)]
    g["lts"] = np.maximum(np.where(g["can_fw"], g["lts_fw"], 0), np.where(g["can_bw"], g["lts_bw"], 0))
    g = roadside(g)
    for d in ("fw", "bw"):
        g.loc[g[f"roadside_{d}"], f"lts_{d}"] = g.loc[g[f"roadside_{d}"], f"lts_{d}"].clip(lower=2)
    g["lts"] = np.maximum(np.where(g["can_fw"], g["lts_fw"], 0), np.where(g["can_bw"], g["lts_bw"], 0))
    g = junctions(g, nodes)

    road = g["highway"].isin(ROADS)
    print("LTS (km, worst allowed direction):", g.groupby("lts")["length_m"].sum().div(1000).round(0).to_dict())
    print("facility fw (km):", g.groupby("fac_fw")["length_m"].sum().div(1000).round(0).to_dict())
    split_dir = road & (g["lts_fw"] != g["lts_bw"]) & g["can_fw"] & g["can_bw"]
    print(f"roads where the two directions differ in stress: {g.loc[split_dir, 'length_m'].sum() / 1000:,.1f} km")
    g.drop(columns=["structure"]).to_parquet(PROC / "cycle_edges.parquet")
    pd.DataFrame(nodes, columns=["x", "y"]).to_parquet(PROC / "cycle_nodes.parquet")


if __name__ == "__main__":
    main()
