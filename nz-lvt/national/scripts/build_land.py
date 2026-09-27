"""Combine harvested rating units into a national land dataset, then summarise by SA2 and TA.

Steps
  1. Load every data/raw/land/*.parquet, assign each rating unit to a territorial
     authority, an SA2 (2023) and a Stats NZ urban/rural area by point-in-polygon.
  2. Adjust the two partial sources:
       - ECan 2021 values (six Canterbury districts) are scaled by the ratio of current to
         2021 land value observed in Selwyn and Waimakariri, the nearest comparable
         districts that have both.
       - Upper Hutt publishes capital value only: LV = CV x the LV/CV ratio of Hutt City
         units in the same capital-value decile.
  3. Classify land for the Tax Reset:
       - rural (0.5%): valuation category starting A/D/P/H/F (arable, dairy, pastoral,
         horticulture, forestry) or L (lifestyle); where a layer has no category, land in a
         Stats NZ "Rural other" area. Everything else is urban (1.75%).
       - residential: category R (or L, lifestyle), or, where a layer has no category, a
         unit whose CV is within the typical dwelling range of its SA2 (0.25x-4x the
         SA2 median CV) in an urban area or rural settlement. This rule is checked against
         the categorised councils in the output.
  4. Flag units inside DOC public conservation land as exempt (unless categorised
     residential), since TOP exempts conservation land.
  5. Summarise by SA2 and TA. TAs with no public layer are listed so the model can impute.

Coverage is checked against LINZ's official rating-unit counts per TA.

Writes data/processed/land_units.parquet, sa2_land.csv, ta_land.csv, land_coverage.csv.
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
ECAN_URL = ("https://services1.arcgis.com/RNxkQaMWQcgbiF98/arcgis/rest/services/"
            "Rates_Calculator_Datasets_Public/FeatureServer/0/query")
RURAL_CATS = set("ADPHF")
RES_CV_BAND = (0.25, 4.0)


def ascii_name(name):
    import unicodedata
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()


def load_units():
    frames = [pd.read_parquet(p) for p in sorted((RAW / "land").glob("*.parquet"))]
    u = pd.concat(frames, ignore_index=True)
    u = u[u["x"].notna() & u["y"].notna()]
    return gpd.GeoDataFrame(u, geometry=gpd.points_from_xy(u["x"], u["y"]), crs=2193)


def join(u, path, cols, nearest=False):
    poly = gpd.read_file(path).to_crs(2193)[cols + ["geometry"]]
    j = gpd.sjoin(u[["geometry"]], poly, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    missing = j[cols[0]].isna()
    if nearest and missing.any():
        k = gpd.sjoin_nearest(u.loc[missing, ["geometry"]], poly, how="left", max_distance=2_000)
        k = k[~k.index.duplicated()]
        j.loc[k.index, cols] = k[cols]
    return j[cols]


def ecan_scale(u):
    """Ratio of current LV to ECan 2021 LV for Selwyn + Waimakariri."""
    cache = RAW / "ecan_2021_totals.json"
    if not cache.exists():
        stats = [{"statisticType": "sum", "onStatisticField": "LandValue", "outStatisticFieldName": "lv"}]
        r = requests.get(ECAN_URL, params={"where": "LocalCouncil IN ('Selwyn','Waimakariri')",
                                           "outStatistics": json.dumps(stats),
                                           "groupByFieldsForStatistics": "LocalCouncil", "f": "json"}, timeout=120)
        cache.write_text(json.dumps({f["attributes"]["LocalCouncil"]: f["attributes"]["lv"]
                                     for f in r.json()["features"]}))
    old = sum(json.loads(cache.read_text()).values())
    new = u.loc[u["source"].isin(["selwyn", "waimakariri"]), "lv"].sum()
    return new / old


def impute_upper_hutt(u):
    hutt = u[(u["source"] == "hutt") & (u["cv"] > 0) & (u["lv"] > 0)]
    edges = np.unique(np.quantile(hutt["cv"], np.linspace(0, 1, 11)))
    ratio = (hutt["lv"] / hutt["cv"]).groupby(pd.cut(hutt["cv"], edges, include_lowest=True)).median()
    m = u["source"] == "upper_hutt"
    bins = pd.cut(u.loc[m, "cv"].clip(edges[0], edges[-1]), edges, include_lowest=True)
    u.loc[m, "lv"] = u.loc[m, "cv"] * bins.map(ratio).astype(float)
    return u


def classify(u):
    first = u["cat"].fillna("").str.upper().str[:1]
    has_cat = first.str.match(r"[A-Z]")
    rural_area = u["iur"].eq("Rural other")
    u["rural"] = np.where(has_cat, first.isin(RURAL_CATS | {"L"}), rural_area)

    sa2_med = u.groupby("sa2")["cv"].transform("median")
    in_band = u["cv"].between(RES_CV_BAND[0] * sa2_med, RES_CV_BAND[1] * sa2_med)
    settled = u["iur"].isin(["Major urban area", "Large urban area", "Medium urban area",
                             "Small urban area", "Rural settlement"])
    u["residential"] = np.where(has_cat, first.isin(["R", "L"]), in_band & settled)
    u["has_cat"] = has_cat
    return u


def check_residential_rule(u):
    """How well does the no-category rule reproduce category-based residential LV by SA2?"""
    c = u[u["has_cat"] & u["sa2"].notna()].copy()
    med = c.groupby("sa2")["cv"].transform("median")
    settled = c["iur"].isin(["Major urban area", "Large urban area", "Medium urban area",
                             "Small urban area", "Rural settlement"])
    c["rule"] = c["cv"].between(RES_CV_BAND[0] * med, RES_CV_BAND[1] * med) & settled
    g = c.groupby("sa2")
    truth = g.apply(lambda d: d.loc[d["residential"], "lv"].mean())
    rule = g.apply(lambda d: d.loc[d["rule"], "lv"].mean())
    ok = truth.notna() & rule.notna()
    err = (rule[ok] / truth[ok] - 1).abs()
    return pd.Series({"sa2s_tested": int(ok.sum()), "median_abs_error_%": err.median() * 100,
                      "p90_abs_error_%": err.quantile(0.9) * 100})


def main():
    PROC.mkdir(parents=True, exist_ok=True)
    u = load_units()
    u[["ta_code", "ta_name"]] = join(u, RAW / "ta_2025.geojson", ["ta_code", "ta_name"], nearest=True)
    u["sa2"] = join(u, RAW / "sa2_2023_clipped.geojson", ["SA22023_V1_00"], nearest=True)["SA22023_V1_00"]
    u["iur"] = join(u, RAW / "urban_rural_2023.geojson", ["IUR2023_V1_00_NAME"], nearest=True)["IUR2023_V1_00_NAME"]

    scale = ecan_scale(u)
    u.loc[u["source"] == "ecan_2021", "lv"] *= scale
    u.loc[u["source"] == "ecan_2021", "cv"] *= scale
    print(f"ECan 2021 -> current scale factor {scale:.2f}")
    u = impute_upper_hutt(u)
    # Regional layers overlap a few single-council layers at the edges: keep one row per
    # valuation number per TA, preferring the council's own layer.
    u = u[(u["lv"] > 0) & u["ta_name"].notna()]
    u = u.drop_duplicates(["ta_name", "vid"])
    u = classify(u)
    # DOC public conservation land is exempt. Some council layers value it (e.g. national
    # parks in Southland); without a category it would otherwise be counted as farmland.
    doc = gpd.read_file(RAW / "doc_conservation_land.geojson").to_crs(2193)[["geometry"]]
    inside = gpd.sjoin(u[["geometry"]], doc, how="inner", predicate="within").index.unique()
    u["conservation"] = u.index.isin(inside) & ~(u["has_cat"] & u["residential"])
    u.loc[u["conservation"], ["rural", "residential"]] = False
    print(f"conservation land exempted: {u['conservation'].sum():,} units, "
          f"LV ${u.loc[u['conservation'], 'lv'].sum() / 1e9:.1f}bn")
    u.drop(columns="geometry").to_parquet(PROC / "land_units.parquet")

    print("residential rule check (category councils):")
    print(check_residential_rule(u).round(1).to_string())

    def summary(g):
        return pd.Series({
            "units": len(g), "lv": g["lv"].sum(), "cv": g["cv"].sum(),
            "lv_conservation": g.loc[g["conservation"], "lv"].sum(),
            "lv_rural": g.loc[g["rural"], "lv"].sum(),
            "lv_urban": g.loc[~g["rural"] & ~g["conservation"], "lv"].sum(),
            "res_units": int(g["residential"].sum()), "lv_res": g.loc[g["residential"], "lv"].sum(),
            "lv_res_rural": g.loc[g["residential"] & g["rural"], "lv"].sum(),
            "lv_farm": g.loc[g["rural"] & ~g["residential"], "lv"].sum(),
            "sources": ",".join(sorted(g["source"].unique())),
        })

    sa2 = u.groupby("sa2").apply(summary)
    sa2["lv_res_per_unit"] = sa2["lv_res"] / sa2["res_units"].replace(0, np.nan)
    sa2.to_csv(PROC / "sa2_land.csv")
    ta = u.groupby("ta_name").apply(summary)
    ta.to_csv(PROC / "ta_land.csv")

    all_ta = gpd.read_file(RAW / "ta_2025.geojson")["ta_name"]
    all_ta = all_ta[~all_ta.str.contains("Area Outside", case=False)]
    cov = pd.DataFrame({"ta_name": sorted(all_ta)})
    cov["units"] = cov["ta_name"].map(ta["units"]).fillna(0).astype(int)
    cov["lv_bn"] = cov["ta_name"].map(ta["lv"] / 1e9).round(2)
    cov["sources"] = cov["ta_name"].map(ta["sources"]).fillna("")
    # Official rating-unit counts (LINZ NZ Property Boundaries, "NZ Unit of Property").
    linz = json.loads((RAW / "linz_rating_units_by_ta.json").read_text())
    cov["linz_units"] = cov["ta_name"].map(lambda n: linz.get(ascii_name(n)))
    cov["unit_coverage_%"] = (cov["units"] / cov["linz_units"] * 100).round(0)
    cov.to_csv(PROC / "land_coverage.csv", index=False)
    print(f"{len(u):,} rating units, LV ${u['lv'].sum() / 1e9:,.0f}bn, "
          f"{(cov['units'] > 500).sum()} of {len(cov)} TAs covered")
    print(cov.to_string(index=False))


if __name__ == "__main__":
    main()
