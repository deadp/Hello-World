"""Clean the Wellington valuation roll into one row per rateable rating unit.

Steps
  1. Drop zero-value parent records (unit-title and cross-lease parents whose value
     sits on the child units) and valued parents whose portions (rating divisions,
     e.g. "COMMERCIAL PORTION" / "RESIDENTIAL PORTION") sum to the parent. Keeping
     the parent as well would double count ~$6.5bn.
  2. Attach the operative 2024 District Plan zone via each unit's representative point.
  3. Flag units that are probably non-rateable (open space / town belt / hospital /
     tertiary / corrections zones, schools). The public layer has no rateability
     field, so this is a proxy; see README for the check against QV's totals.
  4. Assign a proxy differential rating category (Base vs Commercial, Industrial &
     Business). The public layer has no category field either; rules below.
  5. Assign a descriptive property type for reporting.

Writes data/processed/units.parquet (GeoParquet, NZTM 2000).
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "units.parquet"

# Zones where the default use is commercial / industrial / business.
COMMERCIAL_ZONES = {
    "CCZ", "MCZ", "LCZ", "NCZ", "MUZ", "GIZ", "PORTZ", "AIRPZ", "WFZ", "STADZ", "QUARZ",
}
# Zones dominated by land the council treats as non-rateable (reserves, town belt)
# or largely non-rateable institutions.
NON_RATEABLE_ZONES = {"OSZ", "NOSZ", "WTBZ", "HOSZ", "TEDZ", "CORZ"}

# Inside commercial zones, units at or below these capital values that look like
# dwellings are treated as Base (residential). Chosen so the commercial share of
# capital value lands near the ~15% published by Business Central for 2025/26.
RES_UNIT_MAX_CV = 1_500_000
RES_HOUSE_MAX_CV = 2_000_000

VACANT_IMPROVEMENT_RATIO = 0.05  # improvements < 5% of land value counts as (near-)vacant


def load():
    p = gpd.read_file(RAW / "property.geojson").to_crs(2193)
    z = gpd.read_file(RAW / "zones.geojson").to_crs(2193)
    z = z[z["Status"] != "Proposed"][["DPZoneCode", "DPZone", "geometry"]]
    return p, z


def drop_parents(p):
    p = p.copy()
    p["parcel_id"] = p["RollNumber"] + "-" + p["AssessmentNumber"]
    has_children = set(p.loc[p["AssessmentSuffix"].notna(), "parcel_id"])
    parent = p["AssessmentSuffix"].isna() & p["parcel_id"].isin(has_children)
    zero = p["CapitalValue"] <= 0
    print(f"dropping {int((parent & ~zero).sum())} valued parents with portions, "
          f"{int(zero.sum())} zero-value records")
    return p[~parent & ~zero].copy()


def attach_zone(p, z):
    pts = p[["geometry"]].copy()
    pts["geometry"] = p.geometry.representative_point()
    j = gpd.sjoin(pts, z, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    p["zone"] = j["DPZoneCode"].fillna("NONE")
    return p


def classify(p):
    legal = p["LegalDescription"].fillna("").str.upper()
    addr = p["FullAddress"].fillna("").str.upper()
    cv, iv, lv = p["CapitalValue"], p["ImprovementsValue"], p["LandValue"]

    is_unit = legal.str.contains(r"\b(?:UNIT|FLAT)\b") | addr.str.match(r"^(?:UNIT|FLAT|APT)\b")
    portion = legal.str.extract(r"\b([A-Z]+) PORTION")[0]
    school = legal.str.contains(r"\bSCHOOL|\bCOLLEGE\b|KINDERGARTEN|KURA\b")
    church = legal.str.contains(r"\bCHURCH|RELIGIOUS|CHARITABLE PORTION")
    crown_other = legal.str.contains(r"PARLIAMENT|\bMARAE\b|EDUCATION(?:AL)? (?:USE|PORTION)")

    p["non_rateable_proxy"] = (
        p["zone"].isin(NON_RATEABLE_ZONES)
        | school
        | church
        | crown_other
        | portion.isin(["EDUCATIONAL", "EDUCATION", "CHURCH", "CHARITABLE", "RELIGIOUS"])
    )

    in_comm_zone = p["zone"].isin(COMMERCIAL_ZONES)
    res_unit = is_unit & (cv <= RES_UNIT_MAX_CV)
    house_like = ~is_unit & (cv < RES_HOUSE_MAX_CV) & (iv > 0)
    commercial = in_comm_zone & ~(res_unit | house_like)
    # Explicit portion labels override the zone rule.
    commercial |= portion.isin(["COMMERCIAL", "RETAIL", "INDUSTRIAL", "OFFICE", "SHOP", "HOTEL"])
    commercial &= ~portion.isin(["RESIDENTIAL", "RESIDENTAL", "FARM", "RURAL"])
    p["category"] = np.where(commercial, "Commercial", "Base")

    vacant = (iv < VACANT_IMPROVEMENT_RATIO * lv) & (lv > 0)
    ptype = np.select(
        [
            commercial & vacant,
            commercial,
            vacant,
            p["zone"].isin(["GRUZ"]),
            is_unit,
        ],
        [
            "Commercial land, little or no building",
            "Commercial",
            "Residential vacant land",
            "Rural / lifestyle",
            "Apartment / unit / flat",
        ],
        default="House",
    )
    p["ptype"] = ptype
    p["is_unit"] = is_unit
    return p


def main():
    p, z = load()
    p = drop_parents(p)
    p = attach_zone(p, z)
    p = classify(p)
    keep = [
        "ValuationID", "parcel_id", "FullAddress", "Suburb", "LegalDescription", "LandArea",
        "CapitalValue", "LandValue", "ImprovementsValue", "zone", "category", "ptype",
        "is_unit", "non_rateable_proxy", "geometry",
    ]
    p = p[keep]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p.to_parquet(OUT)

    r = p[~p["non_rateable_proxy"]]
    print(f"all units: {len(p):,}  CV ${p.CapitalValue.sum()/1e9:.2f}bn  LV ${p.LandValue.sum()/1e9:.2f}bn")
    print(f"rateable proxy: {len(r):,}  CV ${r.CapitalValue.sum()/1e9:.2f}bn  LV ${r.LandValue.sum()/1e9:.2f}bn")
    share = r.groupby("category")[["CapitalValue", "LandValue"]].sum()
    print((share / share.sum()).round(3))
    print(r["ptype"].value_counts())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
