"""Download Wellington City's road and three-waters networks from the council's public GIS.

Layers (gis.wcc.govt.nz):
  - Transportation/Roads/MapServer/4: road centrelines with RAMM category (arterial,
    collector, local, private, ...), length.
  - PropertyAndBoundaries/Downtown_Levy_Area/MapServer/6: area where the downtown levy applies.
  - PropertyAndBoundaries/Parcels/MapServer/2: road and rail parcels (the road reserve).
  - PropertyAndBoundaries/BuildingFootprints/MapServer/0: building footprints with approximate
    height (roof area for stormwater; floor area for commercial activity).
  - WaterServices/WCC_3_Waters_Underground_Services_Backup/MapServer/13, 19, 25: water,
    wastewater and stormwater pipes with owner, diameter, material, install date.
Only in-use pipes are kept. Writes GeoParquet (NZTM 2000) to data/raw/networks/.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import shape

BASE = "https://gis.wcc.govt.nz/arcgis/rest/services"
PIPES = f"{BASE}/WaterServices/WCC_3_Waters_Underground_Services_Backup/MapServer"
LAYERS = {
    "downtown_levy_area": (f"{BASE}/PropertyAndBoundaries/Downtown_Levy_Area/MapServer/6", "1=1", "*"),
    "road_parcels": (f"{BASE}/PropertyAndBoundaries/Parcels/MapServer/2", "1=1", "parcel_id,type"),
    "buildings": (f"{BASE}/PropertyAndBoundaries/BuildingFootprints/MapServer/0", "1=1", "approx_hei,Status"),
    "roads": (f"{BASE}/Transportation/Roads/MapServer/4", "1=1",
              "feature_id,location,category,ONRC,suburb,adt,prim_meas"),
    "water_pipes": (f"{PIPES}/13", "Operational_Status = 'In Use'",
                    "Asset_ID,Pipe_Type,Pipe_Use,Owner,Diameter_mm,Length_m,Material,Date_Installed,Condition_Grade"),
    "wastewater_pipes": (f"{PIPES}/19", "Operational_Status = 'In Use'",
                         "Asset_ID,Pipe_Type,Pipe_Use,Owner,Diameter_mm,Length_m,Material,Date_Installed,Condition_Grade"),
    "stormwater_pipes": (f"{PIPES}/25", "Operational_Status = 'In Use'",
                         "Asset_ID,Pipe_Type,Pipe_Use,Owner,Diameter_mm,Length_m,Material,Date_Installed,Condition_Grade"),
}
# Stats NZ 2024 business demography employee counts by SA2 (2023 boundaries), clipped to
# Wellington City's extent; used to calibrate commercial floor area to jobs.
EMPLOYEES = ("https://services2.arcgis.com/vKb0s8tBIA3bdocZ/arcgis/rest/services/"
             "2024_Business_Demography_employee_count_by_SA2/FeatureServer/0")
WCC_BBOX = {"geometry": "174.61,-41.37,174.90,-41.14", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "maxAllowableOffset": 5}
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "networks"
PAGE = 2000


def get(url, params, tries=5):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=180)
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise ValueError(d["error"])
            return d
        except (requests.RequestException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def fetch(name, url, where, fields, extra=None):
    extra = extra or {}
    count = get(f"{url}/query", {"where": where, "returnCountOnly": "true", "f": "json", **extra})["count"]
    oid = next(f["name"] for f in get(url, {"f": "json"})["fields"] if f["type"] == "esriFieldTypeOID")

    def one(offset):
        return get(f"{url}/query", {"where": where, "outFields": fields, "orderByFields": oid,
                                    "resultOffset": offset, "resultRecordCount": PAGE, "returnGeometry": "true",
                                    "outSR": 2193, "geometryPrecision": 1, "f": "geojson", **extra})["features"]

    rows = []
    with ThreadPoolExecutor(6) as ex:
        for feats in ex.map(one, range(0, count, PAGE)):
            for f in feats:
                if f.get("geometry"):
                    rows.append({**f["properties"], "geometry": shape(f["geometry"])})
    if len(rows) < 0.99 * count:
        raise RuntimeError(f"{name}: got {len(rows)} of {count}")
    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=2193)
    g.to_parquet(OUT / f"{name}.parquet")
    print(f"{name}: {len(g):,} features, {g.geometry.length.sum() / 1000:,.0f} km")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (url, where, fields) in LAYERS.items():
        fetch(name, url, where, fields)
    fetch("employees_sa2", EMPLOYEES, "1=1", "SA22023_V1_00,SA22023_V1_00_NAME_ASCII,ec2024", WCC_BBOX)
