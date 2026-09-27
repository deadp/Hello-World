"""Download 2023 Census SA2 tables for all of New Zealand from Stats NZ's public ArcGIS services.

Tables (attributes only, unclipped layers):
  households   household income bands + median, tenure, landlord sector, weekly rent, composition
  individuals1 usually resident population, 5-year age groups
  individuals2 personal income bands + median, income sources (incl. NZ Super, main benefits)
And SA2 2023 boundaries clipped to the coastline (mapping) and territorial authority 2025
boundaries (to assign each SA2 to its council), and Stats NZ Urban Rural 2023 areas (to
split urban from rural land where a council layer has no property category), and DOC public
conservation land (exempt from the land value tax).

Stats NZ confidentialises small counts by random rounding and suppresses some cells with
negative codes (e.g. -999); those are kept as-is here and cleaned in build scripts.

Writes CSV / GeoJSON to data/raw/.
"""

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

STATSNZ = "https://services2.arcgis.com/vKb0s8tBIA3bdocZ/arcgis/rest/services"
TABLES = {
    "households": f"{STATSNZ}/2023_Census_totals_by_topic_for_households_by_SA2/FeatureServer/1",
    "individuals1": f"{STATSNZ}/2023_Census_totals_by_topic_for_individuals_by_SA2/FeatureServer/1",
    "individuals2": f"{STATSNZ}/2023_Census_totals_by_topic_for_individuals_by_SA2/FeatureServer/3",
}
# Keep only 2023 fields whose alias matches these topics.
KEEP = re.compile(
    r"Year: 2023.*(income|Tenure of household|Sector of landlord|Weekly rent|Household composition"
    r"|Age \(5-year groups|Census usually resident population count \(Total\))",
    re.I,
)
BOUNDARIES = f"{STATSNZ}/2023_Census_totals_by_topic_for_households_by_SA2/FeatureServer/0"
# Stats NZ territorial authority 2025 boundaries, republished on Wellington City Council's GIS.
DOC_LAND = "https://services1.arcgis.com/3JjYDyG3oajxU6HO/arcgis/rest/services/DOC_Public_Conservation_Land/FeatureServer/0"
URBAN_RURAL = f"{STATSNZ}/Urban_Rural_Areas_2023/FeatureServer/0"
TA_BOUNDARIES = "https://gis.wcc.govt.nz/arcgis/rest/services/StateOfHousing/StateOfHousing/MapServer/59"
PAGE = 1000
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def get(url, params, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=180)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def pages(url, fields, geometry=False, fmt="json", extra=None):
    count = get(f"{url}/query", {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
    out = []
    for offset in range(0, count, PAGE):
        page = get(f"{url}/query", {
            "where": "1=1", "outFields": fields, "returnGeometry": str(geometry).lower(),
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE,
            "outSR": 4326, "geometryPrecision": 5, "f": fmt, **(extra or {}),
        })
        out.extend(page["features"])
        time.sleep(0.3)
    if len(out) != count:
        raise RuntimeError(f"{url}: expected {count}, got {len(out)}")
    return out


def fetch_table(name, url):
    meta = get(url, {"f": "json"})
    fields = [f for f in meta["fields"] if f["name"].startswith("SA2") or KEEP.search(f.get("alias", ""))]
    names = [f["name"] for f in fields]
    rows = [f["attributes"] for f in pages(url, ",".join(names))]
    df = pd.DataFrame(rows)[names]
    df.to_csv(RAW / f"census2023_sa2_{name}.csv", index=False)
    pd.DataFrame({"field": names, "alias": [f.get("alias", "") for f in fields]}).to_csv(
        RAW / f"census2023_sa2_{name}_fields.csv", index=False)
    print(f"{name}: {len(df)} SA2s x {len(names)} fields")


def fetch_boundaries():
    feats = pages(BOUNDARIES, "SA22023_V1_00,SA22023_V1_00_NAME_ASCII", geometry=True, fmt="geojson")
    (RAW / "sa2_2023_clipped.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"boundaries: {len(feats)} SA2s")
    feats = pages(TA_BOUNDARIES, "ta_code,ta_name,rc_code,rc_name", geometry=True, fmt="geojson")
    (RAW / "ta_2025.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"territorial authorities: {len(feats)}")
    feats = pages(URBAN_RURAL, "UR2023_V1_00,UR2023_V1_00_NAME_ASCII,IUR2023_V1_00,IUR2023_V1_00_NAME",
                  geometry=True, fmt="geojson")
    (RAW / "urban_rural_2023.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"urban/rural areas: {len(feats)}")
    # Generalised to ~0.0005 deg (~50 m): only used to exempt rating units inside it.
    feats = pages(DOC_LAND, "Type,Name", geometry=True, fmt="geojson", extra={"maxAllowableOffset": 0.0005})
    (RAW / "doc_conservation_land.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"DOC public conservation land: {len(feats)}")


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in TABLES.items():
        fetch_table(name, url)
    fetch_boundaries()
