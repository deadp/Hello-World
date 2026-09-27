"""Download Wellington City Council's public rating valuation and zoning layers.

Sources (WCC ArcGIS REST services, public):
  - PropertyAndBoundaries/Property/MapServer/0: one row per rating unit with
    CapitalValue, LandValue, ImprovementsValue (2024 revaluation, rates from 1 July 2025).
  - 2024DistrictPlan/2024DistrictPlan/MapServer/122: operative district plan zones.
  - StateOfHousing/StateOfHousing/MapServer/57: Stats NZ SA2 2025 boundaries (Wellington City only).
Stats NZ ArcGIS services (public), clipped to the Wellington City SA2 extent:
  - Statistical_Area_1_2025: SA1 2025 boundaries.
  - 2023_Census_totals_by_topic_for_individuals_by_SA1: 2023 usually resident population
    (attributes only).

Writes GeoJSON files to data/raw/.
"""

import json
import time
from pathlib import Path

import requests

BASE = "https://gis.wcc.govt.nz/arcgis/rest/services"
STATSNZ = "https://services2.arcgis.com/vKb0s8tBIA3bdocZ/arcgis/rest/services"
LAYERS = {
    "property": (f"{BASE}/PropertyAndBoundaries/Property/MapServer/0", "1=1"),
    "zones": (f"{BASE}/2024DistrictPlan/2024DistrictPlan/MapServer/122", "1=1"),
    "sa2": (f"{BASE}/StateOfHousing/StateOfHousing/MapServer/57", "ta_name = 'Wellington City'"),
}
# Fetched after sa2, filtered to its bounding box (these layers carry no council field).
STATSNZ_LAYERS = {
    "sa1": (f"{STATSNZ}/Statistical_Area_1_2025/FeatureServer/0", "*", True),
    "sa1_census2023": (
        f"{STATSNZ}/2023_Census_totals_by_topic_for_individuals_by_SA1/FeatureServer/1",
        "SA12023_V1_00,VAR_1_3",  # VAR_1_3 = 2023 census usually resident population
        False,
    ),
}
PAGE = 2000
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def get(url, params, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def fetch_layer(name, url, where, bbox=None, out_fields="*", geometry=True):
    spatial = {}
    if bbox is not None:
        spatial = {
            "geometry": ",".join(str(v) for v in bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        }
    count = get(f"{url}/query", {"where": where, "returnCountOnly": "true", "f": "json", **spatial})["count"]
    features = []
    for offset in range(0, count, PAGE):
        page = get(
            f"{url}/query",
            {
                "where": where,
                "outFields": out_fields,
                "returnGeometry": str(geometry).lower(),
                "outSR": 4326,
                "geometryPrecision": 6,
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": PAGE,
                "f": "geojson",
                **spatial,
            },
        )
        features.extend(page["features"])
        print(f"{name}: {len(features)}/{count}", flush=True)
        time.sleep(0.5)
    if len(features) != count:
        raise RuntimeError(f"{name}: expected {count} features, got {len(features)}")
    out = RAW / f"{name}.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print(f"wrote {out}")


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    for name, (url, where) in LAYERS.items():
        fetch_layer(name, url, where)
    sa2 = json.loads((RAW / "sa2.geojson").read_text())
    xs, ys = [], []
    for feat in sa2["features"]:
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for x, y in poly[0]:
                xs.append(x)
                ys.append(y)
    bbox = (min(xs), min(ys), max(xs), max(ys))
    for name, (url, fields, geometry) in STATSNZ_LAYERS.items():
        fetch_layer(name, url, "1=1", bbox=bbox, out_fields=fields, geometry=geometry)
