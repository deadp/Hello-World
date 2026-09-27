"""Download Wellington City's road and three-waters networks from the council's public GIS.

Layers (gis.wcc.govt.nz):
  - Transportation/Roads/MapServer/4: road centrelines with RAMM category (arterial,
    collector, local, private, ...), length.
  - PropertyAndBoundaries/Downtown_Levy_Area/MapServer/6: area where the downtown levy applies.
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
    "roads": (f"{BASE}/Transportation/Roads/MapServer/4", "1=1",
              "feature_id,location,category,ONRC,suburb,adt,prim_meas"),
    "water_pipes": (f"{PIPES}/13", "Operational_Status = 'In Use'",
                    "Asset_ID,Pipe_Type,Pipe_Use,Owner,Diameter_mm,Length_m,Material,Date_Installed"),
    "wastewater_pipes": (f"{PIPES}/19", "Operational_Status = 'In Use'",
                         "Asset_ID,Pipe_Type,Pipe_Use,Owner,Diameter_mm,Length_m,Material,Date_Installed"),
    "stormwater_pipes": (f"{PIPES}/25", "Operational_Status = 'In Use'",
                         "Asset_ID,Pipe_Type,Pipe_Use,Owner,Diameter_mm,Length_m,Material,Date_Installed"),
}
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


def fetch(name, url, where, fields):
    count = get(f"{url}/query", {"where": where, "returnCountOnly": "true", "f": "json"})["count"]

    def one(offset):
        return get(f"{url}/query", {"where": where, "outFields": fields, "orderByFields": "OBJECTID",
                                    "resultOffset": offset, "resultRecordCount": PAGE, "returnGeometry": "true",
                                    "outSR": 2193, "geometryPrecision": 1, "f": "geojson"})["features"]

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
