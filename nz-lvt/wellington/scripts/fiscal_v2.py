"""Version 2 of the rates vs cost-of-service model, using only public data.

Same revenue side and activity cost totals as fiscal.py (views A and B). What changes is how
costs are assigned to properties:

Local networks (network_v2.py)
  - Pipes carry asset-based annual cost weights (replacement cost by diameter / life by
    material x condition), so a 300 mm concrete main costs more than a 100 mm PE rider main.
    The local/trunk split comes from those weights instead of a fixed 2.5x trunk multiplier.
  - Water mains and service connections are charged to the properties traced as connected.
  - Local pipes in the road reserve: by frontage. Local pipes crossing private land: a
    neighbourhood pool spread over the rating units in the SA1 they sit in.
  - Roads: fronting properties pay the local-street equivalent only; the extra cost of
    wider, busier roads is a citywide transport cost.

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

Uncertainty: variants with worker weights halved/doubled, a 60% people share, and
off-road pipes pooled citywide rather than by SA1.

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
from figures import INK, INK2, SERIES, title  # noqa: E402,F401

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
DEFAULT = dict(pe_w=0.35, trip_w=0.8, svc_w=0.1, people_share=PEOPLE_SHARE, offroad="sa1")
VARIANTS = {
    "v2": {},
    "workers_low": dict(pe_w=0.2, trip_w=0.5, svc_w=0.05),
    "workers_high": dict(pe_w=0.7, trip_w=1.0, svc_w=0.2),
    "people_share_60": dict(people_share=0.6),
    "offroad_citywide": dict(offroad="city"),
}


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


def allocate(u, net, act, scale, p):
    """Cost per rateable unit for one view (dict of activity totals) and parameter set."""
    t = json.loads((PROC / "network_totals_v2.json").read_text())
    rate = u[~u["non_rateable_proxy"]]
    pe = rate["residents"] + p["pe_w"] * rate["workers"]
    trips = rate["residents"] + p["trip_w"] * rate["workers"]
    people = rate["residents"] + p["svc_w"] * rate["workers"]
    cost = pd.DataFrame(0.0, index=rate.index, columns=["local", "trunk", "transport", "sector", "other"])
    parcel_on = net.set_index("wkb")
    sa1_pool = pd.read_parquet(PROC / "sa1_network_v2.parquet")
    rate_sa1 = rate["wkb"].map(parcel_on["sa1"])
    ledger = {}

    def spread(amount, weight):
        return amount * weight / weight.sum()

    def by_parcel(col, per_w):
        on = rate["wkb"].map(parcel_on[col]).fillna(0) * rate["cv_share"] * per_w
        return on

    for a, total in act.items():
        total *= scale
        network = total * F.NETWORK_SHARE[a]
        if a == "transport":
            per_w = network / t["road_total_w"]
            local = by_parcel("road_front", per_w)
            cost["local"] += local
            pool = total - local.sum()
            cost["transport"] += spread(pool, trips)
            ledger[a] = dict(total=total, local=local.sum(), by_trips=pool)
            continue
        cols = [f"{a}_front"] + (["water_traced"] if a == "water" else [])
        local_w = sum(t[f"{c}_w"] for c in cols) + t[f"{a}_offroad_w"]
        per_w = network / (local_w + t[f"{a}_trunk_w"])
        local = sum(by_parcel(c, per_w) for c in cols)
        off_total = t[f"{a}_offroad_w"] * per_w
        if p["offroad"] == "sa1":
            pools = sa1_pool[f"{a}_sa1"] * per_w
            n_in = rate_sa1.map(rate_sa1.value_counts())
            off = (rate_sa1.map(pools) / n_in).fillna(0)
        else:
            off = spread(off_total, pe if a != "stormwater" else rate["impervious"])
        cost["local"] += local + off
        rest = total - local.sum() - off.sum()  # trunk, treatment, non-rateable frontage, untraced
        weight = rate["impervious"] if a == "stormwater" else pe
        cost["trunk"] += spread(rest, weight)
        ledger[a] = dict(total=total, local=local.sum(), offroad=off.sum(), by_demand=rest)

    cv = rate["CapitalValue"]
    base = rate["category"] == "Base"
    for name, mask in [("base_sector", base), ("commercial_sector", ~base), ("downtown", rate["downtown"] & ~base)]:
        levy = F.SECTOR_LEVIES[name] * scale
        cost["sector"] += np.where(mask, levy * cv / cv[mask].sum(), 0)
    other = (F.RATES_TOTAL - sum(F.ACT_A.values()) - sum(F.SECTOR_LEVIES.values())) * scale
    cost["other"] += spread(other * p["people_share"], people) + other * (1 - p["people_share"]) / len(rate)
    ledger["other_services"] = dict(total=other, by_people=other * p["people_share"])
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

    out = rate[["ValuationID", "FullAddress", "Suburb", "ptype", "category", "CapitalValue", "LandValue", "rates",
                "residents", "workers", "floor", "impervious"]].copy()
    for name, over in VARIANTS.items():
        p = {**DEFAULT, **over}
        ca, la = allocate(u, net, F.ACT_A, scale, p)
        cb, lb = allocate(u, net, F.ACT_B, scale, p)
        out[f"cost_A_{name}"] = ca.sum(axis=1)
        out[f"cost_B_{name}"] = cb.sum(axis=1)
        if name == "v2":
            for c in ca.columns:
                out[f"A_{c}"] = ca[c]
            led = pd.concat({"A": la, "B": lb}, axis=1) / 1e6
            led.round(1).to_csv(TABLES / "fiscal_v2_cost_pools_$m.csv")
            print(led.round(1).to_string())
    out = out.set_index("ValuationID")
    out["cost_A_v1"] = v1["cost_A"].reindex(out.index)
    out["cost_B_v1"] = v1["cost_B"].reindex(out.index)
    out["density"] = v1["density"].reindex(out.index)
    out.to_parquet(PROC / "fiscal_units_v2.parquet")

    names = ["v1"] + list(VARIANTS)

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
    print(by_sub[["units", "ratio_A_v1", "ratio_A_v2", "v2_range_low", "v2_range_high", "net_A_v1_$m",
                  "net_A_v2_$m"]].round(2).to_string())
    sub_all = compare(out, "Suburb")
    print("Tawa, all property:", sub_all.loc["Tawa", ["ratio_A_v1", "ratio_A_v2", "net_A_v1_$m",
                                                     "net_A_v2_$m"]].round(2).to_dict())
    figure(by_type, by_density)


def figure(by_type, by_density):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    for ax, df, lab, ttl in [(axes[0], by_type, list(by_type.index), "By property type"),
                             (axes[1], by_density, [f"{i} dwellings/ha" for i in by_density.index],
                              "Residential, by density")]:
        y = np.arange(len(df))
        ax.barh(y - 0.2, df["ratio_A_v1"], height=0.38, color=SERIES[1], label="v1 (frontage, per unit)")
        ax.barh(y + 0.2, df["ratio_A_v2"], height=0.38, color=SERIES[0], label="v2 (assets, demand)")
        ax.errorbar(df["ratio_A_v2"], y + 0.2, xerr=[df["ratio_A_v2"] - df["v2_range_low"],
                                                     df["v2_range_high"] - df["ratio_A_v2"]],
                    fmt="none", ecolor=INK, elinewidth=1, capsize=3)
        for yy, v in zip(y + 0.2, df["ratio_A_v2"]):
            ax.text(df["v2_range_high"].max() * 0.02 + max(v, df["v2_range_high"].loc[df.index[int(yy)]]),
                    yy, f"{v:.2f}", va="center", fontsize=8.5, color=INK2)
        ax.axvline(1, color=INK, lw=1)
        ax.set_yticks(y, lab)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Rates paid ÷ cost to serve (rates-funded view)")
        ax.set_title(ttl, loc="left", fontsize=11, color=INK2)
    axes[0].legend(loc="lower right", frameon=False, fontsize=9)
    fig.suptitle("Cost model v1 vs v2, Wellington City 2025/26 (bars on v2: range across variants)", x=0.02,
                 ha="left", fontsize=13, fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(FIGS / "fiscal_v2_vs_v1.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
