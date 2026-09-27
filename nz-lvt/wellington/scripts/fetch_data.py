"""Download Wellington City Council's public rating valuation and zoning layers.

Sources (WCC ArcGIS REST services, public):
  - PropertyAndBoundaries/Property/MapServer/0: one row per rating unit with
    CapitalValue, LandValue, ImprovementsValue (2024 revaluation, rates from 1 July 2025).
  - 2024DistrictPlan/2024DistrictPlan/MapServer/122: operative district plan zones.
  - StateOfHousing/StateOfHousing/MapServer/57: Stats NZ SA2 2025 boundaries (Wellington City only).

Writes GeoJSON files to data/raw/.
"""

import json
import time
from pathlib import Path

import requests

BASE = "https://gis.wcc.govt.nz/arcgis/rest/services"
LAYERS = {
    "property": (f"{BASE}/PropertyAndBoundaries/Property/MapServer/0", "1=1"),
    "zones": (f"{BASE}/2024DistrictPlan/2024DistrictPlan/MapServer/122", "1=1"),
    "sa2": (f"{BASE}/StateOfHousing/StateOfHousing/MapServer/57", "ta_name = 'Wellington City'"),
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


def fetch_layer(name, url, where):
    count = get(f"{url}/query", {"where": where, "returnCountOnly": "true", "f": "json"})["count"]
    features = []
    for offset in range(0, count, PAGE):
        page = get(
            f"{url}/query",
            {
                "where": where,
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": 4326,
                "geometryPrecision": 6,
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": PAGE,
                "f": "geojson",
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
