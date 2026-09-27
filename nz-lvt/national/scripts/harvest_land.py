"""Harvest rating-unit land values from public council and regional-council ArcGIS layers.

There is no public national valuation roll (LINZ's full District Valuation Roll is
restricted to government agencies), but about 50 of New Zealand's 67 territorial
authorities publish rating-unit land value (LV) and capital value (CV) on public map
services, mostly through regional councils. This script downloads each layer, keeps one
row per valuation number, reduces geometry to a single point (NZTM 2000) and writes
data/raw/land/<source>.parquet with columns:
    source, vid, lv, cv, cat, x, y
`cat` is the valuation roll category code (first letter R/L/C/I/A/D/P/H/F/S/O/U/M) where
the layer carries one, otherwise empty.

Special cases
  - Auckland uses the 2024 revaluation fields (LLV/LCV).
  - Queenstown-Lakes comes from QLDC's own layer (the Otago regional layer is incomplete
    there); its numeric land-use code is mapped to a category letter.
  - Western Bay of Plenty stores LV and CV as text in two separate layers, joined here.
  - Upper Hutt publishes CV only; LV is imputed later (see build_land.py).
  - ECan's layer carries 2021 values; it is only used for the six Canterbury districts
    that have no current layer, and scaled later.
  - Wellington City comes from the Wellington analysis (../wellington).

Usage: python scripts/harvest_land.py [source ...]   (default: all, skipping cached files)
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "land"
WELLINGTON_RAW = ROOT.parent / "wellington" / "data" / "raw" / "property.geojson"

# name: (layer URL, valuation-id field, LV field, CV field, category field or None, where)
SOURCES = {
    "auckland": ("https://services1.arcgis.com/n4yPwebTjJCmXB6W/arcgis/rest/services/AGOL_RateAccountInfo1_gdb/FeatureServer/0",
                 "VALUATIONREF", "LLV", "LCV", None, "1=1"),
    "waikato_rc": ("https://maps.waikatoregion.govt.nz/arcgis/rest/services/WRCMAPS/Base_Layers_Ext/MapServer/0",
                   "VG_NUMBER", "LAND_VALUE", "CAPITAL_VALUE", "CATEGORY_CODE", "1=1"),
    "otago_rc": ("https://maps.orc.govt.nz/arcgis/rest/services/PropertyExternal/MapServer/0",
                 "ValuationReference", "LandValue", "CapitalValue", None,
                 "RatingAuthority NOT IN ('NA', 'Queenstown Lakes District')"),
    # The Otago layer misses ~25% of Queenstown-Lakes land value; use QLDC's own layer.
    "queenstown_lakes": ("https://services1.arcgis.com/9YyqaQtDdDR8tupG/arcgis/rest/services/Land_Parcels_and_Properties_Data/FeatureServer/0",
                         "ASSESSMENT_NO", "LAND_VALUE", "CAPITAL_VALUE", "LANDUSE", "1=1"),
    "horizons": ("https://maps.horizons.govt.nz/arcgis/rest/services/LocalMapsPublic/Public_Property/MapServer/4",
                 "valuation_reference_ascii", "LandValue", "CapitalValue", None, "1=1"),
    "taranaki": ("https://services.arcgis.com/MMPHUPU6MnEt0lEK/arcgis/rest/services/Property_Rating/FeatureServer/0",
                 "Assessment", "Land_Value", "Capital_Value", None, "1=1"),
    "southland": ("https://services3.arcgis.com/v5RzLI7nHYeFImL4/arcgis/rest/services/SouthlandProperties/FeatureServer/0",
                  "ValuationNumber", "LandValue", "CapitalValue", None, "1=1"),
    "wairarapa": ("https://services.arcgis.com/A7cbKwgfOOpgi0u0/arcgis/rest/services/PropertyPublic/FeatureServer/0",
                  "ValuationID", "LandValue", "CapitalValue", None, "1=1"),
    "west_coast": ("https://utility.arcgis.com/usrsvcs/servers/f3457ab380c242b9a47e52e6dd46f9c1/rest/services/Hosted/Public_Property_and_Rates_View/FeatureServer/0",
                   "valuation_number", "land_value", "capital_value", None, "valuation_number IS NOT NULL"),
    "ecan_2021": ("https://services1.arcgis.com/RNxkQaMWQcgbiF98/arcgis/rest/services/Rates_Calculator_Datasets_Public/FeatureServer/0",
                  "ValuationNo", "LandValue", "CapitalValue", "Category",
                  "LocalCouncil IN ('Ashburton','Timaru','Hurunui','Waimate','Mackenzie','Kaikoura')"),
    "christchurch": ("https://gis.ccc.govt.nz/server/rest/services/OpenData/Property/FeatureServer/21",
                     "ValuationReference", "LandValue", "CapitalValue", None, "1=1"),
    "selwyn": ("https://gis.selwyn.govt.nz/arcgis/rest/services/SDC_Public/Property_Public/MapServer/0",
               "Assessment_ID", "LandValue", "CapitalValue", None, "1=1"),
    "waimakariri": ("https://gisservices.waimakariri.govt.nz/arcgis/rest/services/Property/PropertyandLand/MapServer/5",
                    "VNZ", "LANDVALUE", "CapitalValue", None, "1=1"),
    "tauranga": ("https://gis.tauranga.govt.nz/server/rest/services/RM_PropertySearch/MapServer/1",
                 "VNZ", "LANDVAL", "CAPVAL", "CATEGORY", "1=1"),
    "wbop": ("https://map.westernbay.govt.nz/arcgisext/rest/services/Property/MapServer/5",
             "ValuationNumber", "LandValue", None, None, "1=1"),
    "whangarei": ("https://geo.wdc.govt.nz/server/rest/services/Property__Land__Roads_and_Rail_public_view/FeatureServer/12",
                  "as_assess_no", "as_lv", "as_cv", "rup_category_code", "1=1"),
    "gisborne": ("https://services7.arcgis.com/8G10QCd84QpdcTJ9/arcgis/rest/services/rating_valuation/FeatureServer/0",
                 "ASSESSMNT", "LandValue", "CapitalValue", "DVRCategoryCode", "1=1"),
    "tasman": ("https://gispublic.tasman.govt.nz/server/rest/services/OpenData/OpenData_Property/MapServer/0",
               "ValuationAssessment", "LandValue", "CapitalValue", "CategoryCodeValNZ", "1=1"),
    "marlborough": ("https://gis.marlborough.govt.nz/server/rest/services/OpenData/OpenData2/MapServer/18",
                    "assessment_no", "LandValue", "CapitalValue", None, "1=1"),
    "hutt": ("https://services1.arcgis.com/DlsnLEhGfXazS5Er/arcgis/rest/services/Hutt_City_Properties_view/FeatureServer/1",
             "valuation", "land_value", "capital_value", "rating_category", "1=1"),
    "porirua": ("https://maps.poriruacity.govt.nz/server/rest/services/Property/PropertyAdminExternal/MapServer/5",
                "Valuation_No", "Land_Value", "Total_Value", None, "1=1"),
    "kapiti": ("https://maps.kapiticoast.govt.nz/server/rest/services/Public/Property_Public/MapServer/0",
               "ValuationID", "LandValue", "CapitalValue", None, "1=1"),
    "central_hawkes_bay": ("https://services8.arcgis.com/O26DwMB5KWy6K8pZ/arcgis/rest/services/Property_Master_Map_WFL1/FeatureServer/3",
                           "valuation_id", "land_value", "capital_value", None, "1=1"),
    "opotiki": ("https://services1.arcgis.com/YALYaa5XlEAVUh55/arcgis/rest/services/LINZ_Data/FeatureServer/0",
                "Assessment", "LandValue", "CapitalValue", None, "1=1"),
    "upper_hutt": ("https://services1.arcgis.com/H0o1MSouS8mlA3T1/arcgis/rest/services/UHCC_Properties_Rates_2026/FeatureServer/0",
                   "VNZ_No", None, "y_26_27_Capital_Value", None, "1=1"),
}
# Layers whose category field is the numeric DVR land-use code: map its first digit to the
# category letter used elsewhere (1 primary industry, 2 lifestyle, 7 industrial,
# 8 commercial, 9 residential; anything else other).
USE_CODE_SOURCES = {"queenstown_lakes"}
USE_TO_CAT = {"1": "P", "2": "L", "7": "I", "8": "C", "9": "R"}
WBOP_CV = "https://map.westernbay.govt.nz/arcgisext/rest/services/Property/MapServer/4"
PAGE = 1000
SIMPLIFY_M = 25  # server-side generalisation; we only need a point inside each parcel

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "nz-lvt-analysis (research)"


def get(url, params, tries=5):
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=180)
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise ValueError(d["error"])
            return d
        except (requests.RequestException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def num(v):
    if v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.]", "", str(v))
    return float(s) if s else np.nan


def point(geom):
    if not geom:
        return np.nan, np.nan
    if "x" in geom:
        return geom["x"], geom["y"]
    rings = geom.get("rings")
    if not rings:
        return np.nan, np.nan
    ring = max(rings, key=len)
    if len(ring) < 3:
        return ring[0][0], ring[0][1]
    try:
        p = Polygon(ring)
        if not p.is_valid:
            p = p.buffer(0)
        rp = p.representative_point()
        return rp.x, rp.y
    except Exception:
        xs, ys = zip(*ring)
        return float(np.mean(xs)), float(np.mean(ys))


def fetch(url, fields, where, geometry=True):
    meta = get(url, {"f": "json"})
    oid = next(f["name"] for f in meta["fields"] if f["type"] == "esriFieldTypeOID")
    page = min(meta.get("maxRecordCount") or PAGE, 2000)
    count = get(f"{url}/query", {"where": where, "returnCountOnly": "true", "f": "json"})["count"]

    def one(offset):
        p = {"where": where, "outFields": ",".join(fields), "orderByFields": oid,
             "resultOffset": offset, "resultRecordCount": page, "f": "json",
             "returnGeometry": str(geometry).lower()}
        if geometry:
            p.update({"outSR": 2193, "geometryPrecision": 0, "maxAllowableOffset": SIMPLIFY_M})
        return get(f"{url}/query", p)["features"]

    rows = []
    with ThreadPoolExecutor(6) as ex:
        for feats in ex.map(one, range(0, count, page)):
            for f in feats:
                a = f["attributes"]
                if geometry:
                    a["_x"], a["_y"] = point(f.get("geometry"))
                rows.append(a)
    if len(rows) < count:
        raise RuntimeError(f"{url}: got {len(rows)} of {count}")
    return pd.DataFrame(rows)


def harvest(name):
    url, vid, lv, cv, cat, where = SOURCES[name]
    fields = [f for f in (vid, lv, cv, cat) if f]
    df = fetch(url, fields, where)
    out = pd.DataFrame({
        "source": name,
        "vid": df[vid].astype(str).str.strip(),
        "lv": df[lv].map(num) if lv else np.nan,
        "cv": df[cv].map(num) if cv else np.nan,
        "cat": df[cat].astype(str).str.strip() if cat else "",
        "x": df["_x"],
        "y": df["_y"],
    })
    if name in USE_CODE_SOURCES:
        out["cat"] = out["cat"].str.strip().str.zfill(2).str[0].map(USE_TO_CAT).fillna("O")
    if name == "wbop":
        cvdf = fetch(WBOP_CV, ["ValuationNumber", "CapitalValue"], "1=1", geometry=False)
        cvmap = cvdf.assign(v=cvdf["CapitalValue"].map(num)).groupby(
            cvdf["ValuationNumber"].astype(str).str.strip())["v"].max()
        out["cv"] = out["vid"].map(cvmap)
    return finish(out)


def finish(out):
    out = out[out["vid"].notna() & ~out["vid"].isin(["", "None", "nan", "Road"])]
    # Parcel-based layers repeat a rating unit on every parcel it covers: keep one row.
    out = out.sort_values("lv", ascending=False).drop_duplicates("vid")
    return out.reset_index(drop=True)


def harvest_wellington():
    import geopandas as gpd
    p = gpd.read_file(WELLINGTON_RAW).to_crs(2193)
    parcel = p["RollNumber"] + "-" + p["AssessmentNumber"]
    has_children = set(parcel[p["AssessmentSuffix"].notna()])
    p = p[~(p["AssessmentSuffix"].isna() & parcel.isin(has_children))]
    pt = p.geometry.representative_point()
    return finish(pd.DataFrame({"source": "wellington_city", "vid": p["ValuationID"], "lv": p["LandValue"],
                                "cv": p["CapitalValue"], "cat": "", "x": pt.x, "y": pt.y}))


def main(names):
    OUT.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = OUT / f"{name}.parquet"
        if path.exists():
            print(f"{name}: cached")
            continue
        t = time.time()
        try:
            df = harvest_wellington() if name == "wellington_city" else harvest(name)
        except Exception as e:  # keep going; report at the end
            print(f"{name}: FAILED {e}")
            continue
        df.to_parquet(path)
        print(f"{name}: {len(df):,} units, LV ${df['lv'].sum() / 1e9:,.1f}bn, CV ${df['cv'].sum() / 1e9:,.1f}bn "
              f"({time.time() - t:.0f}s)", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or list(SOURCES) + ["wellington_city"])
