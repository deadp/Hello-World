"""Version 2 network attribution: asset-based cost weights and better assignment to properties.

Changes from network_frontage.py (v1):
  1. Each pipe gets an annual cost weight from its own attributes instead of a flat
     cost per metre: replacement cost (length x unit rate by diameter) / service life
     (by material) x a condition factor (grade 1 = 0.8 ... grade 5 = 1.5).
  2. Water: service connections are traced to the main they join; a main's cost is
     split equally among the properties actually connected to it, and each connection's
     own cost goes to its property.
  3. Pipes are tested against the road reserve (WCC road and rail parcels). Local pipes
     in the road reserve are charged by frontage as before. Local pipes on private land
     (crossing hillside sections) go to a neighbourhood pool, spread over the rating
     units of their SA1, instead of landing on the section they cross.
  4. Roads are weighted by category (carriageway width proxy) and traffic (ADT). Fronting
     properties pay only the local-street equivalent (weight 1.0 per metre, both sides);
     the rest of an arterial's cost is a citywide transport cost.

Outputs:
  data/processed/parcel_network_v2.parquet  per parcel (wkb): road_front, water_front,
      water_traced, wastewater_front, stormwater_front (annual cost weights)
  data/processed/sa1_network_v2.parquet     per SA1: off-road local pipe weights
  data/processed/network_totals_v2.json     totals by class, incl. trunk and road excess,
      plus implied replacement cost of the pipe inventory vs the council's valuation.
Weights are relative; fiscal_v2.py scales each network to its activity cost.
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from network_frontage import ROAD_LOCAL, ROAD_SHARED, attribute, sample_points

ROOT = Path(__file__).resolve().parents[1]
NET = ROOT / "data" / "raw" / "networks"
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"

# Replacement cost per metre by diameter (NZ$ 2024, open-trench urban; relative shape
# matters more than level, which is reported against the council's valuation).
UNIT_RATE = [(50, 350), (100, 700), (150, 900), (225, 1150), (300, 1450), (450, 2000), (600, 2700),
             (900, 3800), (np.inf, 5500)]
LIFE = {"AC": 60, "CU": 60, "CI": 90, "DI": 90, "DICL": 90, "STCL": 70, "ST": 70, "STEL": 70, "GALV": 50,
        "EW": 90, "RCON": 80, "CONC": 80, "BRICK": 100}
PLASTIC_LIFE = 100
DEFAULT_LIFE = 80
CONDITION = {1: 0.8, 2: 0.9, 3: 1.0, 4: 1.25, 5: 1.5}
LOCAL_MAX_D = 300
LOCAL_TYPES = {"Main", "RIderMain", "Service Connection", "Sump Lead", "Culvert"}
ROAD_WEIGHT = {"Local": 1.0, "Residential": 1.0, "Sub-Collector": 1.2, "Rural 1": 0.8, "Rural 2": 0.7,
               "Rural 3": 0.6, "Service Lane": 0.6, "Suburban Shopping": 1.4, "Central City Shopping": 1.6,
               "Central City Business": 1.6, "Central City Golden Mile": 1.8, "Pedestrian Mall": 1.2,
               "Carpark": 0.8, "Tawa Driveway": 0.5, "Collector": 1.6, "Principal": 2.0, "Arterial": 2.2}


def unit_rate(d):
    d = pd.to_numeric(d, errors="coerce").fillna(150)
    return np.select([d <= u for u, _ in UNIT_RATE], [r for _, r in UNIT_RATE])


def life(material):
    m = material.fillna("").str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    plastic = m.str.contains("PE|PVC|PP|GRP")
    return np.where(plastic, PLASTIC_LIFE, m.map(LIFE).fillna(DEFAULT_LIFE))


def cost_weights(p):
    """Annual cost weight ($/yr) and replacement cost for each pipe."""
    length = p.geometry.length
    rc = length * unit_rate(p["Diameter_mm"])
    cond = pd.to_numeric(p["Condition_Grade"], errors="coerce").map(CONDITION).fillna(1.0)
    return rc / life(p["Material"]) * cond, rc


def in_road(lines, road):
    """Share of three sample points (25/50/75%) inside the road reserve."""
    pts = []
    for f in (0.25, 0.5, 0.75):
        pts.append(gpd.GeoDataFrame({"i": lines.index}, geometry=lines.geometry.interpolate(f, normalized=True),
                                    crs=lines.crs))
    pts = pd.concat(pts)
    hit = gpd.sjoin(pts, road, predicate="within", how="left")
    hit = hit[~hit.index.duplicated()] if False else hit
    inside = hit.groupby("i")["index_right"].apply(lambda s: s.notna().mean())
    return inside.reindex(lines.index).fillna(0) >= 0.5


def parcels_table():
    u = gpd.read_parquet(PROC / "units.parquet")
    u["wkb"] = u.geometry.to_wkb()
    parcels = u.drop_duplicates("wkb")[["wkb", "geometry"]].reset_index(drop=True)
    parcels["pid"] = parcels.index
    sa1 = gpd.read_file(RAW / "sa1.geojson").to_crs(2193)[["SA12025_V1_00", "geometry"]]
    pts = gpd.GeoDataFrame(geometry=parcels.geometry.representative_point(), index=parcels.index, crs=2193)
    j = gpd.sjoin(pts, sa1, predicate="within")
    parcels["sa1"] = j["SA12025_V1_00"].reindex(parcels.index)
    return parcels


def trace_water(water, parcels):
    """Map service connections to mains and properties. Returns (main->pids, conn->pid)."""
    conns = water[water["Pipe_Type"] == "Service Connection"]
    mains = water[water["Pipe_Type"].isin(["Main", "RIderMain"]) & (water["dia"] <= LOCAL_MAX_D)]
    ends = []
    for which, fn in (("a", lambda g: g.interpolate(0)), ("b", lambda g: g.interpolate(1, normalized=True))):
        ends.append(gpd.GeoDataFrame({"conn": conns.index, "end": which}, geometry=fn(conns.geometry), crs=2193))
    ends = pd.concat(ends, ignore_index=True)
    near_main = gpd.sjoin_nearest(ends, mains[["geometry"]].assign(main=mains.index), how="left",
                                  max_distance=3, distance_col="dmain")
    near_main = near_main.sort_values("dmain").drop_duplicates(["conn", "end"])
    # For each connection the end closest to a main is the tapping; the other end is the property.
    best = near_main.dropna(subset=["main"]).sort_values("dmain").drop_duplicates("conn")
    prop_end = ends.merge(best[["conn", "end", "main"]], on="conn", suffixes=("", "_tap"))
    prop_end = prop_end[prop_end["end"] != prop_end["end_tap"]]
    prop_end = gpd.GeoDataFrame(prop_end, geometry="geometry", crs=2193)
    to_parcel = gpd.sjoin_nearest(prop_end, parcels[["pid", "geometry"]], how="left", max_distance=25)
    to_parcel = to_parcel[~to_parcel.index.duplicated()].dropna(subset=["pid"])
    main_pids = to_parcel.groupby("main")["pid"].apply(lambda s: sorted(set(s.astype(int))))
    conn_pid = to_parcel.set_index("conn")["pid"].astype(int)
    print(f"  water: {len(conns):,} service connections, {len(conn_pid):,} traced to a property; "
          f"{len(main_pids):,} of {len(mains):,} local mains have connected properties")
    return main_pids, conn_pid


def main():
    parcels = parcels_table()
    road = gpd.read_parquet(NET / "road_parcels.parquet")[["geometry"]]
    road["geometry"] = road.buffer(2)
    totals = {}
    front_cols, sa1_pools = [], []

    for net in ["water", "wastewater", "stormwater"]:
        p = gpd.read_parquet(NET / f"{net}_pipes.parquet")
        p = p[p["Owner"] == "WCC"].copy()
        p["dia"] = pd.to_numeric(p["Diameter_mm"], errors="coerce").fillna(150)
        p["w"], p["rc"] = cost_weights(p)
        local = p["Pipe_Type"].isin(LOCAL_TYPES) & (p["dia"] <= LOCAL_MAX_D)
        totals[f"{net}_rc_inventory_m"] = float(p["rc"].sum() / 1e6)
        totals[f"{net}_trunk_w"] = float(p.loc[~local, "w"].sum())
        loc = p[local].copy()
        loc["road"] = in_road(loc, road)
        print(net)
        if net == "water":
            main_pids, conn_pid = trace_water(loc, parcels)
            traced = pd.Series(0.0, index=parcels["pid"])
            conn = loc[loc.index.isin(conn_pid.index)]
            traced = traced.add(conn["w"].groupby(conn_pid.reindex(conn.index).values).sum(), fill_value=0)
            per = {m: loc.at[m, "w"] / len(pids) for m, pids in main_pids.items()}
            rows = [(pid, per[m]) for m, pids in main_pids.items() for pid in pids]
            traced = traced.add(pd.DataFrame(rows, columns=["pid", "w"]).groupby("pid")["w"].sum(), fill_value=0)
            done = loc.index.isin(conn_pid.index) | loc.index.isin(main_pids.index)
            front_cols.append(traced.rename("water_traced"))
            totals["water_traced_w"] = float(traced.sum())
            rest = loc[~done]
        else:
            rest = loc
        on_road, off_road = rest[rest["road"]], rest[~rest["road"]]
        pts = sample_points(on_road, (on_road["w"] / on_road.geometry.length.clip(lower=0.1)).values)
        front_cols.append(attribute(pts, parcels, f"{net}_front"))
        mid = gpd.GeoDataFrame({"w": off_road["w"].values}, geometry=off_road.geometry.interpolate(0.5, normalized=True),
                               crs=2193)
        s1 = gpd.sjoin(mid, gpd.read_file(RAW / "sa1.geojson").to_crs(2193)[["SA12025_V1_00", "geometry"]],
                       predicate="within").groupby("SA12025_V1_00")["w"].sum().rename(f"{net}_sa1")
        sa1_pools.append(s1)
        totals[f"{net}_front_w"] = float(on_road["w"].sum())
        totals[f"{net}_offroad_w"] = float(off_road["w"].sum())
        print(f"  {net}: local on road {on_road.geometry.length.sum() / 1000:,.0f} km, off road "
              f"{off_road.geometry.length.sum() / 1000:,.0f} km, trunk {p.loc[~local].geometry.length.sum() / 1000:,.0f} km")

    roads = gpd.read_parquet(NET / "roads.parquet")
    roads = roads[roads["category"].isin(ROAD_LOCAL | ROAD_SHARED)].copy()
    adt = pd.to_numeric(roads["adt"], errors="coerce")
    tf = np.clip(1 + 0.2 * np.log2(adt / 1500), 0.8, 1.8).where(adt > 0, 1.0)
    roads["w_per_m"] = roads["category"].map(ROAD_WEIGHT).fillna(1.0) * tf
    front_per_m = np.minimum(roads["w_per_m"], 1.0)
    length = roads.geometry.length
    totals["road_total_w"] = float((length * roads["w_per_m"]).sum())
    totals["road_front_w"] = float((length * front_per_m).sum())
    totals["road_excess_w"] = totals["road_total_w"] - totals["road_front_w"]
    print("roads")
    front_cols.append(attribute(sample_points(roads, front_per_m.values), parcels, "road_front"))

    out = parcels[["pid", "wkb", "sa1"]].set_index("pid").join(pd.concat(front_cols, axis=1)).fillna(0)
    out.to_parquet(PROC / "parcel_network_v2.parquet")
    pd.concat(sa1_pools, axis=1).fillna(0).to_parquet(PROC / "sa1_network_v2.parquet")
    council_rc = {"water": 2401, "wastewater": 3177, "stormwater": 2365}
    for n, rc in council_rc.items():
        totals[f"{n}_rc_council_m"] = rc
    (PROC / "network_totals_v2.json").write_text(json.dumps(totals, indent=1))
    for n, rc in council_rc.items():
        print(f"{n}: pipe inventory replacement cost ${totals[f'{n}_rc_inventory_m']:,.0f}m vs council "
              f"(asset class) ${rc:,}m")


if __name__ == "__main__":
    main()
