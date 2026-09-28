"""Version 2 of the rates vs cost-of-service model, using only public data.

Same revenue side and activity cost totals as fiscal.py (views A and B). What changes is how
costs are assigned to properties:

Local networks (network_v2.py)
  - Pipes carry asset-based annual cost weights (replacement cost by diameter / life by
    material x condition), so a 300 mm concrete main costs more than a 100 mm PE rider main.
  - Calibration: the pipe inventory is scaled to the council's replacement cost per network
    (Annual Report 2024/25) after setting aside non-pipe assets priced from published counts
    (reservoirs, pump stations, tunnels). Non-pipe assets are trunk. Implied depreciation is
    checked against the council's 2025/26 depreciation (fiscal_v2_calibration.csv).
  - Water mains and service connections are traced to the properties connected to them;
    other local pipes in the road reserve go by frontage; local pipes crossing private land
    are an SA1 pool. Roads: fronting properties get the local-street equivalent only; the
    extra cost of wider, busier roads is a citywide transport cost.
  - Sharing (local_rule): each SA1's local network cost (all of the above) is pooled and
    split among its parcels by lot width, sqrt(lot area), then among units on a parcel by
    CV. Frontage says how much network a neighbourhood needs; which lot fronts which pipe is
    mostly noise (corner lots, rear lots, a main serving the whole street). Variants: own
    frontage (no pooling), lot area, equal per unit. The share landing on non-rateable
    parcels (parks, schools) is shared citywide.

Shared costs by demand instead of equally per rating unit
  - Residents: 2023 census usually-resident population of each SA1, split equally over the
    homes in that SA1 (house, apartment, lifestyle units).
  - Workers: Stats NZ 2024 employee counts by SA2, spread over the floor area
    (building footprint x storeys, storeys = height / 3.3 m) of commercial and
    non-rateable properties in that SA2. Jobs on non-rateable land are left out.
  - Water and wastewater trunk + treatment: person-equivalents = residents + 0.35 x workers.
  - Arterial excess + other transport: trips = residents + 0.8 x workers.
  - Stormwater trunk: impervious area = roof footprint + paved share of the rest of the lot
    (20% residential, 60% commercial), urban zones only.
  - Everything else (parks, libraries, community, arts, governance, ...): 81.6% by people
    (residents + 0.1 x workers) and 18.4% per rating unit (building control, planning,
    governance and rates admin that follow properties).
  - Sector levies as v1 (the levy, spread over payers by CV).

Uncertainty: variants for the sharing rule (frontage / area / per unit), non-pipe asset
values (low / high), a steeper cost curve for trunk pipes (x1.5), worker weights halved or
doubled, and a 60% people share. "v2a" is the first version (frontage, uncalibrated),
kept for comparison.

Writes data/processed/fiscal_units_v2.parquet, outputs/tables/fiscal_v2_*.csv and
outputs/figures/fiscal_v2_*.png.
"""

import json
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import fiscal as F  # noqa: E402
from figures import BLUE_RAMP, INK, INK2, SERIES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
NET = RAW / "networks"
TABLES = ROOT / "outputs" / "tables"
FIGS = ROOT / "outputs" / "figures"

STOREY_M = 3.3
PAVED = dict(Base=0.2, Commercial=0.6)
PEOPLE_SHARE = 0.816
HOMES = {"House", "Apartment / unit / flat", "Rural / lifestyle"}
# Calibration to the council's asset valuation (Annual Report 2024/25, Table 36, replacement cost
# at 30 June 2025, $m). The GIS pipe inventory priced with UNIT_RATE comes to less than half of
# it. Part of the gap is assets that aren't pipes (Infrastructure Strategy 2024: 68 reservoirs and
# 34 pump stations for water; 69 pump stations and ~15 km of tunnels for wastewater, of which
# 1.3 km is in the GIS); these are priced from counts (low / central / high unit costs) and are
# trunk assets. The remainder calibrates the pipe unit rates (a single factor k per network).
COUNCIL_RC = dict(water=2401.1, wastewater=3176.7, stormwater=2365.4)
COUNCIL_DEP = dict(water=34.972, wastewater=49.848, stormwater=23.429)  # 2025/26 LTP FIS, incl. plants
NONPIPE_RC = dict(  # $m: reservoirs x $3/6/10m + pump stations x $1/1.5/2.5m; pumps x $1.5/2.5/4m + tunnels $10/15/25k/m
    low=dict(water=68 * 3 + 34 * 1.0, wastewater=69 * 1.5 + 13.7 * 10, stormwater=0),
    central=dict(water=68 * 6 + 34 * 1.5, wastewater=69 * 2.5 + 13.7 * 15, stormwater=50),
    high=dict(water=68 * 10 + 34 * 2.5, wastewater=69 * 4.0 + 13.7 * 25, stormwater=150),
)
NONPIPE_LIFE = 100  # 2022 three waters valuation: reservoirs 90-117 yrs, pump stations ~100

DEFAULT = dict(pe_w=0.35, trip_w=0.8, svc_w=0.1, people_share=PEOPLE_SHARE, local_rule="width",
               nonpipe="central", trunk_curve=1.0)
# local_rule: how each SA1's local network cost (roads and pipes: frontage + traced + off-road)
#   is shared among its parcels. "width" = sqrt(lot area) (street length a lot needs grows with
#   its width), "area" = lot area, "per_home" = equal per rating unit, "frontage" = no pooling,
#   each parcel keeps its own frontage/traced cost and off-road pipes are split per unit in the SA1.
VARIANTS = {
    "v2": {},
    "rule_frontage": dict(local_rule="frontage"),
    "rule_area": dict(local_rule="area"),
    "rule_per_home": dict(local_rule="per_home"),
    "nonpipe_low": dict(nonpipe="low"),
    "nonpipe_high": dict(nonpipe="high"),
    "trunk_curve_steep": dict(trunk_curve=1.5),
    "workers_low": dict(pe_w=0.2, trip_w=0.5, svc_w=0.05),
    "workers_high": dict(pe_w=0.7, trip_w=1.0, svc_w=0.2),
    "people_share_60": dict(people_share=0.6),
}
# First v2 (frontage, uncalibrated), kept for comparison but not in the uncertainty range.
REFERENCE = {"v2a": dict(local_rule="frontage", nonpipe=None)}


def calibration(t, level="central"):
    """Per network: pipe scale factor k and the non-pipe asset weight (in pipe-weight units)."""
    out = {}
    for n, rc in COUNCIL_RC.items():
        nonpipe = NONPIPE_RC[level][n] if level else 0.0
        k = (rc - nonpipe) / t[f"{n}_rc_inventory_m"]
        out[n] = dict(council_rc=rc, nonpipe_rc=nonpipe, pipe_rc_gis=t[f"{n}_rc_inventory_m"], k=k,
                      nonpipe_w=nonpipe * 1e6 / NONPIPE_LIFE / k if level else 0.0,
                      dep_model=k * t[f"{n}_dep_inventory_m"] + nonpipe / NONPIPE_LIFE,
                      dep_council=COUNCIL_DEP[n])
    return out


def load():
    u = gpd.read_parquet(PROC / "units.parquet")
    u["wkb"] = u.geometry.to_wkb()
    dt = gpd.read_parquet(NET / "downtown_levy_area.parquet").to_crs(2193)
    pts = gpd.GeoDataFrame(geometry=u.geometry.representative_point(), index=u.index, crs=u.crs)
    u["downtown"] = u.index.isin(gpd.sjoin(pts, dt[["geometry"]], predicate="within").index)
    u["units_on_parcel"] = u.groupby("wkb")["CapitalValue"].transform("count")
    u["cv_share"] = (u["CapitalValue"] / u.groupby("wkb")["CapitalValue"].transform("sum")).fillna(
        1 / u["units_on_parcel"])

    # Census residents -> homes in each SA1 (SA1 2023 codes).
    # The census table has no geometry; its SA1 codes match the 2025 boundaries one to one.
    s1 = gpd.read_file(RAW / "sa1.geojson").to_crs(2193)[["SA12025_V1_00", "geometry"]]
    j = gpd.sjoin(pts, s1, predicate="within")
    u["sa1_2023"] = j["SA12025_V1_00"][~j.index.duplicated()].reindex(u.index)
    home = u["ptype"].isin(HOMES) & ~u["non_rateable_proxy"]
    homes_in = u[home].groupby("sa1_2023").size()
    census = gpd.read_file(RAW / "sa1_census2023.geojson", ignore_geometry=True)
    pop = census.set_index("SA12023_V1_00")["VAR_1_3"].astype(float)
    u["residents"] = np.where(home, u["sa1_2023"].map(pop / homes_in), 0.0)
    u["residents"] = u["residents"].fillna(0)

    # Floor area from building footprints (joined to the parcel holding the footprint centroid).
    b = gpd.read_parquet(NET / "buildings.parquet").to_crs(2193)
    b = b[b.geometry.area > 5]
    storeys = np.clip(np.round(pd.to_numeric(b["approx_hei"], errors="coerce").fillna(4) / STOREY_M), 1, 60)
    bp = gpd.GeoDataFrame({"roof": b.geometry.area, "floor": b.geometry.area * storeys},
                          geometry=b.geometry.representative_point(), crs=2193)
    parcels = u.drop_duplicates("wkb")[["wkb", "geometry"]]
    jb = gpd.sjoin(bp, parcels, predicate="within").groupby("wkb")[["roof", "floor"]].sum()
    for c in ["roof", "floor"]:
        u[c] = u["wkb"].map(jb[c]).fillna(0) * u["cv_share"]

    # Workers -> commercial and non-rateable floor area in each SA2.
    e = gpd.read_parquet(NET / "employees_sa2.parquet").to_crs(2193)
    j2 = gpd.sjoin(pts, e[["SA22023_V1_00", "geometry"]], predicate="within")
    u["sa2_2023"] = j2["SA22023_V1_00"][~j2.index.duplicated()].reindex(u.index)
    workplace = (u["category"] != "Base") | u["non_rateable_proxy"]
    wf = u["floor"].where(workplace, 0)
    # Commercial land with little building still hosts some jobs: floor floor-area at 20% of lot.
    wf = np.maximum(wf, np.where(workplace, 0.2 * u.geometry.area / u["units_on_parcel"], 0))
    floor_in = wf.groupby(u["sa2_2023"]).sum()
    jobs = e.set_index("SA22023_V1_00")["ec2024"].astype(float)
    u["workers"] = (wf * u["sa2_2023"].map(jobs / floor_in)).fillna(0)

    # Impervious area.
    lot = u.geometry.area / u["units_on_parcel"]
    paved = np.where(u["category"] == "Base", PAVED["Base"], PAVED["Commercial"])
    u["impervious"] = np.minimum(lot, u["roof"] + paved * np.maximum(lot - u["roof"], 0))
    u.loc[u["zone"] == "GRUZ", "impervious"] = 0
    return u


def parcel_frame(u, net):
    """Per parcel (wkb): SA1, lot area, rateable units."""
    g = u.drop_duplicates("wkb").set_index("wkb")
    P = net.set_index("wkb")[["sa1"]].copy()
    P["area"] = g.geometry.area.reindex(P.index).fillna(0).values
    rate = u[~u["non_rateable_proxy"]]
    P["n_rate"] = rate.groupby("wkb").size().reindex(P.index).fillna(0)
    return P


def local_to_units(rate, P, parcel_cost, offroad_sa1, rule):
    """Share local network cost (per parcel, all parcels) plus off-road SA1 pools to rateable units.

    Returns (cost per rateable unit, amount landing on non-rateable parcels)."""
    total = parcel_cost.sum() + offroad_sa1.sum()
    sa1 = P["sa1"]
    if rule == "frontage":
        per_parcel = parcel_cost
        rate_sa1 = rate["wkb"].map(sa1)
        off = (rate_sa1.map(offroad_sa1) / rate_sa1.map(rate_sa1.value_counts())).fillna(0)
        on = rate["wkb"].map(per_parcel).fillna(0) * rate["cv_share"] + off
        return on, total - on.sum()
    key = {"width": np.sqrt(P["area"]), "area": P["area"],
           "per_home": P["n_rate"].where(P["n_rate"] > 0, 1.0)}[rule]
    pooled = sa1.notna()
    pool = parcel_cost[pooled].groupby(sa1[pooled]).sum().add(offroad_sa1, fill_value=0)
    key_sum = key[pooled].groupby(sa1[pooled]).sum()
    per_parcel = (sa1.map(pool) * key / sa1.map(key_sum)).where(pooled, parcel_cost).fillna(0)
    within = (1 / rate.groupby("wkb")["wkb"].transform("size")) if rule == "per_home" else rate["cv_share"]
    on = rate["wkb"].map(per_parcel).fillna(0) * within
    return on, total - on.sum()


def allocate(u, net, P, act, scale, p):
    """Cost per rateable unit for one view (dict of activity totals) and parameter set."""
    t = json.loads((PROC / "network_totals_v2.json").read_text())
    cal = calibration(t, p["nonpipe"])
    rate = u[~u["non_rateable_proxy"]]
    pe = rate["residents"] + p["pe_w"] * rate["workers"]
    trips = rate["residents"] + p["trip_w"] * rate["workers"]
    people = rate["residents"] + p["svc_w"] * rate["workers"]
    cost = pd.DataFrame(0.0, index=rate.index, columns=["local", "trunk", "transport", "sector", "other"])
    nw = net.set_index("wkb")
    sa1_pool = pd.read_parquet(PROC / "sa1_network_v2.parquet")
    ledger = {}

    def spread(amount, weight):
        return amount * weight / weight.sum()

    for a, total in act.items():
        total *= scale
        network = total * F.NETWORK_SHARE[a]
        if a == "transport":
            per_w = network / t["road_total_w"]
            on, _ = local_to_units(rate, P, nw["road_front"] * per_w, pd.Series(dtype=float), p["local_rule"])
            cost["local"] += on
            pool = total - on.sum()
            cost["transport"] += spread(pool, trips)
            ledger[a] = dict(total=total, local=on.sum(), by_demand=pool)
            continue
        cols = [f"{a}_front"] + (["water_traced"] if a == "water" else [])
        local_w = sum(t[f"{c}_w"] for c in cols) + t[f"{a}_offroad_w"]
        trunk_w = t[f"{a}_trunk_w"] * p["trunk_curve"] + cal[a]["nonpipe_w"]
        per_w = network / (local_w + trunk_w)
        on, _ = local_to_units(rate, P, sum(nw[c] for c in cols) * per_w, sa1_pool[f"{a}_sa1"] * per_w,
                               p["local_rule"])
        cost["local"] += on
        rest = total - on.sum()  # trunk, non-pipe assets, treatment, non-rateable land's share
        cost["trunk"] += spread(rest, rate["impervious"] if a == "stormwater" else pe)
        ledger[a] = dict(total=total, local=on.sum(), local_share_of_network=local_w / (local_w + trunk_w),
                         by_demand=rest)

    cv = rate["CapitalValue"]
    base = rate["category"] == "Base"
    for name, mask in [("base_sector", base), ("commercial_sector", ~base), ("downtown", rate["downtown"] & ~base)]:
        levy = F.SECTOR_LEVIES[name] * scale
        cost["sector"] += np.where(mask, levy * cv / cv[mask].sum(), 0)
    other = (F.RATES_TOTAL - sum(F.ACT_A.values()) - sum(F.SECTOR_LEVIES.values())) * scale
    cost["other"] += spread(other * p["people_share"], people) + other * (1 - p["people_share"]) / len(rate)
    ledger["other_services"] = dict(total=other, by_demand=other * p["people_share"])
    return cost, pd.DataFrame(ledger).T


def main():
    u = load()
    net = pd.read_parquet(PROC / "parcel_network_v2.parquet")
    v1 = pd.read_parquet(PROC / "fiscal_units.parquet").set_index("ValuationID")
    rate = u[~u["non_rateable_proxy"]].copy()
    rate["rates"] = F.rates(rate, rate["downtown"])
    scale = rate["rates"].sum() / F.RATES_TOTAL
    print(f"residents on rateable homes {rate['residents'].sum():,.0f}; workers on rateable property "
          f"{rate['workers'].sum():,.0f} (all jobs in joined SA2s {u['workers'].sum():,.0f})")

    P = parcel_frame(u, net)
    t = json.loads((PROC / "network_totals_v2.json").read_text())
    cal = pd.DataFrame(calibration(t)).T
    cal.round(3).to_csv(TABLES / "fiscal_v2_calibration.csv")
    print(cal.round(2).to_string())
    out = rate[["ValuationID", "FullAddress", "Suburb", "ptype", "category", "CapitalValue", "LandValue", "rates",
                "residents", "workers", "floor", "impervious"]].copy()
    for name, over in {**REFERENCE, **VARIANTS}.items():
        p = {**DEFAULT, **over}
        ca, la = allocate(u, net, P, F.ACT_A, scale, p)
        cb, lb = allocate(u, net, P, F.ACT_B, scale, p)
        out[f"cost_A_{name}"] = ca.sum(axis=1)
        out[f"cost_B_{name}"] = cb.sum(axis=1)
        if name == "v2":
            for c in ca.columns:
                out[f"A_{c}"] = ca[c]
            led = pd.concat({"A": la.drop(columns="local_share_of_network") / 1e6,
                             "B": lb.drop(columns="local_share_of_network") / 1e6}, axis=1)
            led[("A", "local_share_of_network")] = la["local_share_of_network"]
            led.round(3).to_csv(TABLES / "fiscal_v2_cost_pools_$m.csv")
            print(led.round(2).to_string())
        out[f"local_A_{name}"] = ca["local"]
    out = out.set_index("ValuationID")
    out["cost_A_v1"] = v1["cost_A"].reindex(out.index)
    out["cost_B_v1"] = v1["cost_B"].reindex(out.index)
    out["density"] = v1["density"].reindex(out.index)
    out.to_parquet(PROC / "fiscal_units_v2.parquet")

    names = ["v1", "v2a"] + list(VARIANTS)

    def compare(df, by):
        g = df.groupby(by, observed=True)
        s = pd.DataFrame({"units": g.size(), "rates_$m": g["rates"].sum() / 1e6})
        for n in names:
            s[f"ratio_A_{n}"] = g["rates"].sum() / g[f"cost_A_{n}"].sum()
        s["ratio_B_v1"] = g["rates"].sum() / g["cost_B_v1"].sum()
        s["ratio_B_v2"] = g["rates"].sum() / g["cost_B_v2"].sum()
        s["v2_range_low"] = s[[f"ratio_A_{n}" for n in VARIANTS]].min(axis=1)
        s["v2_range_high"] = s[[f"ratio_A_{n}" for n in VARIANTS]].max(axis=1)
        s["net_A_v1_$m"] = (g["rates"].sum() - g["cost_A_v1"].sum()) / 1e6
        s["net_A_v2_$m"] = (g["rates"].sum() - g["cost_A_v2"].sum()) / 1e6
        s["residents_per_unit"] = g["residents"].mean()
        return s

    by_type = compare(out, "ptype")
    by_type.to_csv(TABLES / "fiscal_v2_by_property_type.csv", float_format="%.3g")
    print(by_type.round(2).to_string())
    by_density = compare(out[out["category"] == "Base"], "density")
    by_density.to_csv(TABLES / "fiscal_v2_residential_by_density.csv", float_format="%.3g")
    print(by_density.round(2).to_string())
    homes = out[out["ptype"].isin(["House", "Apartment / unit / flat"])]
    by_sub = compare(homes, "Suburb")
    by_sub = by_sub[by_sub["units"] >= 100].sort_values("ratio_A_v2")
    by_sub.to_csv(TABLES / "fiscal_v2_homes_by_suburb.csv", float_format="%.3g")
    print(by_sub[["units", "ratio_A_v1", "ratio_A_v2a", "ratio_A_v2", "v2_range_low", "v2_range_high", "net_A_v1_$m",
                  "net_A_v2_$m"]].round(2).to_string())
    sub_all = compare(out, "Suburb")
    print("Tawa, all property:", sub_all.loc["Tawa", ["ratio_A_v1", "ratio_A_v2a", "ratio_A_v2", "net_A_v1_$m",
                                                     "net_A_v2_$m"]].round(2).to_dict())
    rules = ["v2a", "rule_frontage", "v2", "rule_area", "rule_per_home"]
    homes_all = out[out["category"] == "Base"]
    loc = pd.concat({"by_type": out.groupby("ptype")[[f"local_A_{r}" for r in rules]].mean(),
                     "homes_by_density": homes_all.groupby("density", observed=True)[
                         [f"local_A_{r}" for r in rules]].mean()})
    loc.columns = ["first v2 (frontage, uncalibrated)", "own frontage", "SA1 pool by lot width (v2)",
                   "SA1 pool by lot area", "SA1 pool per unit"]
    loc.round(0).to_csv(TABLES / "fiscal_v2_local_cost_by_rule.csv")
    print("mean local roads + pipes cost per unit, rates-funded:")
    print(loc.round(0).to_string())
    figure(by_type, by_density)


def figure(by_type, by_density):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.6))
    bars = [("ratio_A_v1", SERIES[1], "v1: frontage, equal per unit"),
            ("ratio_A_v2a", BLUE_RAMP[1], "v2a: asset weights, demand-based sharing"),
            ("ratio_A_v2", SERIES[0], "v2: + calibrated to council valuation, SA1 pooling by lot width")]
    h = 0.26
    for ax, df, lab, ttl in [(axes[0], by_type, list(by_type.index), "By property type"),
                             (axes[1], by_density, [f"{i} dwellings/ha" for i in by_density.index],
                              "Residential, by density")]:
        y = np.arange(len(df))
        for i, (col, colour, name) in enumerate(bars):
            ax.barh(y + (i - 1) * h, df[col], height=h * 0.95, color=colour, label=name)
        ax.errorbar(df["ratio_A_v2"], y + h, xerr=[df["ratio_A_v2"] - df["v2_range_low"],
                                                   df["v2_range_high"] - df["ratio_A_v2"]],
                    fmt="none", ecolor=INK, elinewidth=1, capsize=3)
        pad = df["v2_range_high"].max() * 0.02
        for yy, v, hi in zip(y + h, df["ratio_A_v2"], df["v2_range_high"]):
            ax.text(max(v, hi) + pad, yy, f"{v:.2f}", va="center", fontsize=8.5, color=INK2)
        ax.axvline(1, color=INK, lw=1)
        ax.set_yticks(y, lab)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Rates paid ÷ cost to serve (rates-funded view)")
        ax.set_title(ttl, loc="left", fontsize=11, color=INK2)
    fig.legend(*axes[0].get_legend_handles_labels(), loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.suptitle("Cost model versions, Wellington City 2025/26 (whiskers: range across v2 variants)", x=0.02,
                 ha="left", fontsize=13, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(FIGS / "fiscal_v2_vs_v1.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
