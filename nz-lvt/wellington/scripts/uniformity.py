"""Horizontal-uniformity check on land values (after Doucet's Baltimore analysis).

For every (near-)vacant rating unit with its own land area, compare its land value
per m2 with improved neighbours that are:
  - in the same district plan zone,
  - within 300 m (centroid to centroid),
  - between half and double its land area (land $/m2 falls with lot size).

A ratio near 1.0 means vacant land is valued like the built-on land next door, so a
land value rate would not hand vacant owners a hidden discount.

Writes outputs/tables/uniformity_vacant_vs_neighbours.csv and a summary table.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "processed" / "results.parquet"
TABLES = ROOT / "outputs" / "tables"

RADIUS_M = 300
MIN_NEIGHBOURS = 5


def main():
    u = gpd.read_parquet(RESULTS)
    u["land_area"] = pd.to_numeric(u["LandArea"], errors="coerce")
    lots = u[(~u["is_unit"]) & (u["land_area"] >= 50) & (u["LandValue"] > 0)].copy()
    lots["lv_m2"] = lots["LandValue"] / lots["land_area"]
    lots["vacant"] = lots["ptype"].isin(["Residential vacant land", "Commercial land, little or no building"])
    lots["geometry"] = lots.geometry.centroid

    vac = lots[lots["vacant"]]
    imp = lots[~lots["vacant"]]
    buf = vac[["zone", "land_area", "lv_m2", "geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(RADIUS_M)
    pairs = gpd.sjoin(buf, imp[["zone", "land_area", "lv_m2", "geometry"]], predicate="contains",
                      lsuffix="v", rsuffix="n")
    pairs = pairs[
        (pairs["zone_v"] == pairs["zone_n"])
        & (pairs["land_area_n"] >= 0.5 * pairs["land_area_v"])
        & (pairs["land_area_n"] <= 2.0 * pairs["land_area_v"])
    ]
    g = pairs.groupby(level=0)
    res = pd.DataFrame({"neighbours": g.size(), "neighbour_median_lv_m2": g["lv_m2_n"].median()})
    res = res[res["neighbours"] >= MIN_NEIGHBOURS]
    res = vac.join(res, how="inner")
    res["ratio"] = res["lv_m2"] / res["neighbour_median_lv_m2"]

    cols = ["ValuationID", "FullAddress", "Suburb", "zone", "ptype", "land_area", "LandValue",
            "ImprovementsValue", "lv_m2", "neighbours", "neighbour_median_lv_m2", "ratio"]
    TABLES.mkdir(parents=True, exist_ok=True)
    res[cols].sort_values("ratio").to_csv(TABLES / "uniformity_vacant_vs_neighbours.csv",
                                          index=False, float_format="%.4g")

    def summary(df):
        return pd.Series({
            "vacant_lots_compared": len(df),
            "median_ratio": df["ratio"].median(),
            "p25_ratio": df["ratio"].quantile(0.25),
            "p75_ratio": df["ratio"].quantile(0.75),
            "share_below_0.5_%": (df["ratio"] < 0.5).mean() * 100,
            "share_0.8_to_1.25_%": df["ratio"].between(0.8, 1.25).mean() * 100,
        })

    out = pd.concat({"All": summary(res), **{k: summary(v) for k, v in res.groupby("ptype", observed=True)}},
                    axis=1).T
    out.to_csv(TABLES / "uniformity_summary.csv", float_format="%.3g")
    print(out.round(2).to_string())


if __name__ == "__main__":
    main()
