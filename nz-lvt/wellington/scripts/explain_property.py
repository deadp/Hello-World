"""Break down one or more properties' rates bill and modelled cost of service (fiscal.py).

Usage: python scripts/explain_property.py "Onslow Road" [--type House] [--limit 8] [--v2]
Matches FullAddress (case-insensitive substring). Uses the rates-funded cost view (A).

Revenue lines: 2025/26 rates by component (excl. GST).
Cost lines:
  local roads / water / wastewater / stormwater: the property's frontage share of each
    local network x that network's cost per metre (split by CV among units on a parcel);
  stormwater trunk: share by land area;
  sector rate: the base/commercial sector levy spread by CV (funds sector activities);
  shared services: everything else split equally per rating unit (trunk water and
    wastewater, treatment, arterial roads, other transport, parks, libraries, etc.).
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

import fiscal as F

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
COLS = dict(transport="road_local_m", water="water_local_w", wastewater="wastewater_local_w",
            stormwater="stormwater_local_w")


def rate_lines(r):
    cv = r["CapitalValue"]
    rural = r["zone"] == "GRUZ"
    if r["category"] == "Base":
        b = F.BASE
        return {"General rate": b["general"] * cv, "Water": b["water_fixed"] + b["water"] * cv,
                "Sewerage": b["sewer_fixed"] + b["sewer"] * cv, "Stormwater": 0 if rural else b["storm"] * cv,
                "Base sector rate": b["sector"] * cv}
    c = F.COMM
    return {"General rate": c["general"] * cv, "Water": c["water"] * cv, "Sewerage": c["sewer"] * cv,
            "Stormwater": 0 if rural else c["storm"] * cv, "Commercial sector rate": c["sector"] * cv,
            "Downtown levy": c["downtown"] * cv if r["downtown"] else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--type", default=None)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--v2", action="store_true", help="also show the v2 cost model (fiscal_v2.py)")
    a = ap.parse_args()
    v2 = pd.read_parquet(PROC / "fiscal_units_v2.parquet") if a.v2 else None

    fis = pd.read_parquet(PROC / "fiscal_units.parquet").set_index("ValuationID")
    u = gpd.read_parquet(PROC / "units.parquet")
    u["wkb"] = u.geometry.to_wkb()
    rate = u[~u["non_rateable_proxy"]].set_index("ValuationID")
    front = pd.read_parquet(PROC / "parcel_frontage.parquet").set_index("wkb")
    scale = fis["rates"].sum() / F.RATES_TOTAL
    local = F.local_shares()
    per_w = {k: F.ACT_A[k] * scale * F.NETWORK_SHARE[k] * local[k] / front[c].sum() for k, c in COLS.items()}
    units_on_parcel = rate.groupby("wkb")["CapitalValue"].transform("count")
    cv_share = rate["CapitalValue"] / rate.groupby("wkb")["CapitalValue"].transform("sum")
    # Stormwater trunk: by land area among urban units (same rule as fiscal.allocate).
    area = rate.geometry.area / units_on_parcel
    w = area.where(rate["zone"] != "GRUZ", 0)
    storm_trunk = (F.ACT_A["stormwater"] * scale * F.NETWORK_SHARE["stormwater"] * (1 - local["stormwater"])) * w / w.sum()
    base = rate["category"] == "Base"
    cv = rate["CapitalValue"]

    m = fis["FullAddress"].str.contains(a.pattern, case=False, na=False)
    if a.type:
        m &= fis["ptype"] == a.type
    pick = fis[m].head(a.limit)
    for vid, r in pick.iterrows():
        g = rate.loc[vid]
        fr = front.loc[g["wkb"]] if g["wkb"] in front.index else pd.Series(0.0, index=list(COLS.values()))
        share = cv_share.loc[vid]
        costs = {
            "Local roads": fr["road_local_m"] * per_w["transport"] * share,
            "Local water pipes": fr["water_local_w"] * per_w["water"] * share,
            "Local wastewater pipes": fr["wastewater_local_w"] * per_w["wastewater"] * share,
            "Local stormwater pipes": fr["stormwater_local_w"] * per_w["stormwater"] * share,
            "Stormwater trunk (by land area)": storm_trunk.loc[vid],
        }
        if base.loc[vid]:
            costs["Base sector activities"] = F.SECTOR_LEVIES["base_sector"] * scale * cv.loc[vid] / cv[base].sum()
        else:
            costs["Commercial sector activities"] = F.SECTOR_LEVIES["commercial_sector"] * scale * cv.loc[vid] / cv[~base].sum()
        costs["Shared services (per property)"] = r["cost_A"] - sum(costs.values())
        rl = rate_lines(r)
        print(f"\n{r['FullAddress']}  [{r['ptype']}; CV ${r['CapitalValue']:,.0f}, LV ${r['LandValue']:,.0f}; "
              f"parcel {g.geometry.area:,.0f} m2 shared by {units_on_parcel.loc[vid]} unit(s); "
              f"frontage: road {fr['road_local_m'] * share:.1f} m, pipes (weighted) "
              f"{(fr['water_local_w'] + fr['wastewater_local_w'] + fr['stormwater_local_w']) * share:.1f} m]")
        print("  Rates paid:")
        for k, v in rl.items():
            print(f"    {k:<34}{v:>10,.0f}")
        print(f"    {'Total':<34}{r['rates']:>10,.0f}")
        print("  Modelled cost to serve:")
        for k, v in costs.items():
            print(f"    {k:<34}{v:>10,.0f}")
        print(f"    {'Total':<34}{r['cost_A']:>10,.0f}")
        print(f"  Net (rates - cost): {r['net_A']:+,.0f}   ratio {r['rates'] / r['cost_A']:.2f}")
        if v2 is not None and vid in v2.index:
            q = v2.loc[vid]
            print(f"  v2 cost to serve ({q['residents']:.1f} residents, {q['workers']:.1f} workers, "
                  f"{q['impervious']:,.0f} m2 impervious):")
            for k, lab in [("local", "Local roads + pipes (traced/frontage/SA1)"),
                           ("trunk", "Trunk + treatment (by demand)"), ("transport", "Arterials + other transport"),
                           ("sector", "Sector levies"), ("other", "Other services (people + per unit)")]:
                print(f"    {lab:<42}{q['A_' + k]:>10,.0f}")
            var = [c for c in v2.columns if c.startswith("cost_A_") and c not in ("cost_A_v1", "cost_A_v2a")]
            lo, hi = min(q[c] for c in var), max(q[c] for c in var)
            print(f"    {'Total':<42}{q['cost_A_v2']:>10,.0f}   (variants {lo:,.0f}-{hi:,.0f})")
            print(f"  v2 net: {r['rates'] - q['cost_A_v2']:+,.0f}   ratio {r['rates'] / q['cost_A_v2']:.2f}")
            rules = {"v2": "SA1 pool by lot width", "rule_frontage": "own frontage", "rule_area": "SA1 pool by lot area",
                     "rule_per_home": "SA1 pool per unit", "v2a": "first v2 (frontage, uncalibrated)"}
            print("  Local roads + pipes under each rule: " + "; ".join(
                f"{lab} ${q['local_A_' + k]:,.0f}" for k, lab in rules.items()))


if __name__ == "__main__":
    main()
