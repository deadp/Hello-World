"""Model a revenue-neutral shift of Wellington City's general rate from capital value to land value.

Current system (2025/26 rating policy):
  general rate = rate_in_dollar x capital value x differential
  differential: Base 1.0, Commercial/Industrial/Business 3.7 (no UAGC).
  Base rate: 0.301493 cents per $ of capital value incl. GST (billing category A1C).
Targeted rates (water, sewerage, stormwater, downtown levy, ...) are left unchanged,
so every $ figure here is the general rate only.

Scenarios (all raise the same total general-rate revenue as the status quo):
  cv_3.7   status quo
  lv_3.7   land value base, keep the 3.7 commercial differential
  lv_share land value base, each category keeps its current share of revenue
  cv_2.0   capital value base, commercial differential cut to 2.0
  lv_2.0   land value base, commercial differential cut to 2.0
  lv_1.0   land value base, no differential (a "pure" land value rate)

Writes CSV tables to outputs/tables/ and the per-unit results to data/processed/.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "data" / "processed" / "units.parquet"
TABLES = ROOT / "outputs" / "tables"
RESULTS = ROOT / "data" / "processed" / "results.parquet"

BASE_RATE_CV = 0.00301493  # $ per $ of CV, 2025/26, incl. GST
SCENARIOS = {
    "cv_3.7": ("CapitalValue", 3.7),
    "lv_3.7": ("LandValue", 3.7),
    "lv_share": ("LandValue", None),
    "cv_2.0": ("CapitalValue", 2.0),
    "lv_2.0": ("LandValue", 2.0),
    "lv_1.0": ("LandValue", 1.0),
}
TYPE_ORDER = [
    "House",
    "Apartment / unit / flat",
    "Rural / lifestyle",
    "Residential vacant land",
    "Commercial",
    "Commercial land, little or no building",
]


def differential(category, d):
    return np.where(category == "Commercial", d, 1.0)


def run(u):
    u = u.copy()
    status_quo = BASE_RATE_CV * u["CapitalValue"] * differential(u["category"], 3.7)
    total = status_quo.sum()
    rates = {}
    for name, (base, d) in SCENARIOS.items():
        if d is None:
            bill = pd.Series(0.0, index=u.index)
            for cat, idx in u.groupby("category").groups.items():
                cat_rev = status_quo.loc[idx].sum()
                bill.loc[idx] = cat_rev * u.loc[idx, base] / u.loc[idx, base].sum()
        else:
            weight = u[base] * differential(u["category"], d)
            bill = total * weight / weight.sum()
        u[f"rates_{name}"] = bill
        rates[name] = total / (u[base] * differential(u["category"], d or 1.0)).sum()
    for name in SCENARIOS:
        if name == "cv_3.7":
            continue
        u[f"chg_{name}"] = u[f"rates_{name}"] - u["rates_cv_3.7"]
        u[f"pct_{name}"] = u[f"chg_{name}"] / u["rates_cv_3.7"]
    return u, total, rates


def summarise(u, by, scen):
    g = u.groupby(by, observed=True)
    out = pd.DataFrame(
        {
            "units": g.size(),
            "capital_value_bn": g["CapitalValue"].sum() / 1e9,
            "land_value_bn": g["LandValue"].sum() / 1e9,
            "land_share_of_cv": g["LandValue"].sum() / g["CapitalValue"].sum(),
            "median_rates_now": g["rates_cv_3.7"].median(),
            f"median_rates_{scen}": g[f"rates_{scen}"].median(),
            "median_change_$": g[f"chg_{scen}"].median(),
            "median_change_%": g[f"pct_{scen}"].median() * 100,
            "share_paying_less_%": g[f"chg_{scen}"].apply(lambda s: (s < 0).mean() * 100),
            "total_now_$m": g["rates_cv_3.7"].sum() / 1e6,
            f"total_{scen}_$m": g[f"rates_{scen}"].sum() / 1e6,
        }
    )
    out["total_change_$m"] = out[f"total_{scen}_$m"] - out["total_now_$m"]
    return out


def main():
    TABLES.mkdir(parents=True, exist_ok=True)
    u = gpd.read_parquet(UNITS)
    u = u[~u["non_rateable_proxy"]].copy()
    u["ptype"] = pd.Categorical(u["ptype"], TYPE_ORDER, ordered=True)
    u, total, rates = run(u)
    print(f"modelled general-rate revenue: ${total/1e6:,.1f}m across {len(u):,} rating units")

    # Scenario overview: revenue share by category and residential medians.
    rows = []
    for name, (base, d) in SCENARIOS.items():
        col = f"rates_{name}"
        by_cat = u.groupby("category")[col].sum() / total
        house = u["ptype"] == "House"
        apt = u["ptype"] == "Apartment / unit / flat"
        rows.append(
            {
                "scenario": name,
                "base": base,
                "commercial_differential": d if d is not None else "category shares held",
                "base_rate_cents_per_$": rates[name] * 100 if d is not None else np.nan,
                "commercial_share_%": by_cat.get("Commercial", 0) * 100,
                "median_house_$": u.loc[house, col].median(),
                "median_apartment_$": u.loc[apt, col].median(),
                "vacant_and_carpark_total_$m": u.loc[
                    u["ptype"].isin(["Residential vacant land", "Commercial land, little or no building"]), col
                ].sum() / 1e6,
            }
        )
    overview = pd.DataFrame(rows)
    overview.to_csv(TABLES / "scenario_overview.csv", index=False, float_format="%.4g")
    print(overview.to_string(index=False))

    for scen in ["lv_3.7", "lv_share", "lv_2.0"]:
        summarise(u, "ptype", scen).to_csv(TABLES / f"by_type_{scen}.csv", float_format="%.4g")
    print(summarise(u, "ptype", "lv_3.7").round(2).to_string())

    homes = u[u["ptype"].isin(["House", "Apartment / unit / flat"])]
    sub = summarise(homes, "Suburb", "lv_3.7")
    sub = sub[sub["units"] >= 100].sort_values("median_change_%")
    sub.to_csv(TABLES / "homes_by_suburb_lv_3.7.csv", float_format="%.4g")

    homes = homes.copy()
    homes["cv_decile"] = pd.qcut(homes["CapitalValue"], 10, labels=[f"D{i}" for i in range(1, 11)])
    dec = summarise(homes, "cv_decile", "lv_3.7")
    dec.to_csv(TABLES / "homes_by_cv_decile_lv_3.7.csv", float_format="%.4g")
    print(dec[["units", "median_rates_now", "median_change_$", "median_change_%", "share_paying_less_%"]].round(1).to_string())

    u.to_parquet(RESULTS)
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
