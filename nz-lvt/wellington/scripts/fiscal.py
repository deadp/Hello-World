"""Urban3-style fiscal productivity: what each property pays in rates vs what it costs to serve.

Year: 2025/26, the last year Wellington City's rates included water (from 1 July 2026
Tiaki Wai bills water separately), and the year the 2024 valuations first applied.
All dollars exclude GST.

Revenue: each rateable unit's 2025/26 rates bill from the rates resolution (Council
26 June 2025, clauses 10-11): general rate (Base 0.262168 c/$ CV, Commercial 0.958681),
water (Base $337.76 + 0.053413 c/$; Commercial metered water approximated by the
commercial total per $ CV), sewerage (Base $152.68 + 0.048730 c/$; Commercial
0.228332), stormwater (non-rural; Base 0.048112, Commercial 0.068347), base/commercial
sector rates and the downtown levy. Business improvement district levies (~$0.5m) and
the vacant-land differential are left out.

Costs, two views (activity figures: LTP 2024-34 Amendment for 2025/26, $000):
  A. rates-funded: each activity's rates requirement (water 92,260; wastewater 81,695;
     stormwater 45,121; transport general rates 103,789; sector rates and downtown levy
     as levied; everything else = total rates 627,724 minus those).
  B. full cost: operating cost incl. interest + depreciation, net of non-rates income
     (water 118,896 - 2,990; wastewater 119,569 - 1,756; stormwater 56,300 - 487;
     transport net 117,904). Other activities as in A. B minus A is cost the rates do
     not currently cover (mostly unfunded depreciation on three waters).

Cost drivers
  - Local network (roads, pipes <= 300 mm): parcel frontage from network_frontage.py.
    Cost per network = activity cost x network share x local share, where network share
    is the network component's share of the activity's expense (water 66%, wastewater
    63%, stormwater 100%, transport vehicle + pedestrian network 60%) and local share is
    local / (local + 2.5 x trunk) weighted km for pipes, local / (local + 2 x shared) km
    for roads (trunk mains and arterials cost more per metre).
  - Trunk network, treatment, arterial roads, other transport, everything else: equal
    per rating unit (a household-equivalent). Stormwater trunk: by parcel land area in
    urban zones (runoff proxy). Sensitivity: share these by capital value instead.
  - Frontage landing on non-rateable land (parks, schools, reserves) is spread per
    rating unit, since ratepayers fund it.
  - Base/commercial sector rates and the downtown levy fund sector-specific spending:
    cost = the levy, spread over the paying sector by CV.

Writes outputs/tables/fiscal_*.csv, outputs/figures/fiscal_*.png and
data/processed/fiscal_units.parquet.
"""

import json
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

from figures import BLUE_RAMP, GRID, INK, INK2, SERIES, SURFACE, title  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
NET = ROOT / "data" / "raw" / "networks"
TABLES = ROOT / "outputs" / "tables"
FIGS = ROOT / "outputs" / "figures"

# 2025/26 rates, excl. GST, per $ of capital value unless noted.
BASE = dict(general=0.00262168, water_fixed=337.76, water=0.00053413, sewer_fixed=152.68, sewer=0.00048730,
            storm=0.00048112, sector=0.00025468)
COMM = dict(general=0.00958681, water=(5_318_672 + 868_518 + 28_923_271) / 18_382_126_328, sewer=0.00228332,
            storm=0.00068347, sector=0.00030170, downtown=0.00172428)
RATES_TOTAL = 627_723_899

# Activity costs, $ (2025/26).
ACT_A = dict(water=92_260e3, wastewater=81_695e3, stormwater=45_121e3, transport=103_789e3)
ACT_B = dict(water=118_896e3 - 2_990e3, wastewater=119_569e3 - 1_756e3, stormwater=56_300e3 - 487e3,
             transport=117_904e3)
NETWORK_SHARE = dict(water=78_486 / (78_486 + 40_410), wastewater=75_617 / (75_617 + 43_952), stormwater=1.0,
                     transport=(64_474 + 15_891) / 133_686)
SECTOR_LEVIES = dict(base_sector=18_832_979, commercial_sector=5_475_199, downtown=18_672_467)
TRUNK_WEIGHT, ARTERIAL_WEIGHT = 2.5, 2.0


def rates(u, downtown):
    cv = u["CapitalValue"]
    base = u["category"] == "Base"
    rural = u["zone"] == "GRUZ"
    b = (BASE["general"] * cv + BASE["water_fixed"] + BASE["water"] * cv + BASE["sewer_fixed"] + BASE["sewer"] * cv
         + np.where(rural, 0, BASE["storm"] * cv) + BASE["sector"] * cv)
    c = (COMM["general"] * cv + COMM["water"] * cv + COMM["sewer"] * cv + np.where(rural, 0, COMM["storm"] * cv)
         + COMM["sector"] * cv + np.where(downtown, COMM["downtown"] * cv, 0))
    return pd.Series(np.where(base, b, c), index=u.index)


def local_shares():
    t = json.loads((PROC / "network_totals.json").read_text())
    s = {n: t[f"{n}_local_weighted_km"] / (t[f"{n}_local_weighted_km"] + TRUNK_WEIGHT * t[f"{n}_shared_km"])
         for n in ["water", "wastewater", "stormwater"]}
    s["transport"] = t["road_local_km"] / (t["road_local_km"] + ARTERIAL_WEIGHT * t["road_shared_km"])
    return s


def allocate(u, front, act, scale, shared_by="unit"):
    """Return cost per rateable unit for one set of activity costs."""
    rate = u[~u["non_rateable_proxy"]]
    cost = pd.Series(0.0, index=rate.index)
    local_cost = pd.Series(0.0, index=rate.index)
    shared_pool = 0.0
    local = local_shares()
    col = dict(water="water_local_w", wastewater="wastewater_local_w", stormwater="stormwater_local_w",
               transport="road_local_m")
    rows = {}
    for a, total in act.items():
        total *= scale
        net = total * NETWORK_SHARE[a]
        loc = net * local[a]
        per_w = loc / front[col[a]].sum()
        parcel_cost = front[col[a]] * per_w  # by parcel wkb
        # Split parcel cost over its rateable units by CV share; non-rateable parcels -> shared.
        on_rateable = parcel_cost.reindex(rate["wkb"].unique()).fillna(0)
        cv_share = rate["CapitalValue"] / rate.groupby("wkb")["CapitalValue"].transform("sum")
        part = rate["wkb"].map(on_rateable).fillna(0) * cv_share.fillna(0)
        cost += part
        local_cost += part
        to_shared = loc - on_rateable.sum()
        trunk = net - loc
        other = total - net
        if a == "stormwater":
            urban = rate["zone"] != "GRUZ"
            area = rate.geometry.area / rate.groupby("wkb")["CapitalValue"].transform("count")
            w = area.where(urban, 0)
            cost += trunk * w / w.sum()
            shared_pool += to_shared + other
        else:
            shared_pool += to_shared + trunk + other
        rows[a] = dict(total=total, local_frontage=on_rateable.sum(), non_rateable_frontage=to_shared,
                       trunk_and_other=trunk + other)
    # Sector-specific levies: cost = levy, spread over payers by CV.
    cv = rate["CapitalValue"]
    base = rate["category"] == "Base"
    for name, mask in [("base_sector", base), ("commercial_sector", ~base), ("downtown", rate["downtown"] & ~base)]:
        levy = SECTOR_LEVIES[name] * scale
        cost += np.where(mask, levy * cv / cv[mask].sum(), 0)
        rows[name] = dict(total=levy)
    everything_else = (RATES_TOTAL - sum(ACT_A.values()) - sum(SECTOR_LEVIES.values())) * scale
    rows["other_services"] = dict(total=everything_else)
    shared_pool += everything_else
    if shared_by == "unit":
        cost += shared_pool / len(rate)
    else:
        cost += shared_pool * cv / cv.sum()
    return cost, pd.DataFrame(rows).T, local_cost


def main():
    TABLES.mkdir(parents=True, exist_ok=True)
    u = gpd.read_parquet(PROC / "units.parquet")
    u["wkb"] = u.geometry.to_wkb()
    dt = gpd.read_parquet(NET / "downtown_levy_area.parquet").to_crs(2193)
    pts = gpd.GeoDataFrame(geometry=u.geometry.representative_point(), index=u.index, crs=u.crs)
    u["downtown"] = u.index.isin(gpd.sjoin(pts, dt[["geometry"]], predicate="within").index)
    front = pd.read_parquet(PROC / "parcel_frontage.parquet").set_index("wkb")

    rate = u[~u["non_rateable_proxy"]].copy()
    rate["rates"] = rates(rate, rate["downtown"])
    modelled = rate["rates"].sum()
    scale = modelled / RATES_TOTAL
    print(f"modelled rates ${modelled / 1e6:,.1f}m vs actual ${RATES_TOTAL / 1e6:,.1f}m (scale {scale:.3f}); "
          f"{len(rate):,} rateable units")

    rate["cost_A"], ledger_a, rate["local_network_A"] = allocate(u, front, ACT_A, scale)
    rate["cost_B"], ledger_b, rate["local_network_B"] = allocate(u, front, ACT_B, scale)
    rate["cost_A_cv"], _, _ = allocate(u, front, ACT_A, scale, shared_by="cv")
    for k in ["A", "B", "A_cv"]:
        rate[f"net_{k}"] = rate["rates"] - rate[f"cost_{k}"]
    ledger = pd.concat({"A_rates_funded": ledger_a, "B_full_cost": ledger_b}, axis=1) / 1e6
    ledger.round(1).to_csv(TABLES / "fiscal_cost_pools_$m.csv")
    print(ledger.round(1).to_string())
    print(f"total cost A ${rate['cost_A'].sum() / 1e6:,.1f}m, B ${rate['cost_B'].sum() / 1e6:,.1f}m")

    def summary(df, by):
        g = df.groupby(by, observed=True)
        s = pd.DataFrame({
            "units": g.size(),
            "rates_$m": g["rates"].sum() / 1e6,
            "cost_A_$m": g["cost_A"].sum() / 1e6,
            "cost_B_$m": g["cost_B"].sum() / 1e6,
            "median_rates": g["rates"].median(),
            "median_cost_A": g["cost_A"].median(),
            "median_cost_B": g["cost_B"].median(),
            "local_network_cost_A_per_unit": g["local_network_A"].mean(),
            "local_network_cost_B_per_unit": g["local_network_B"].mean(),
            "revenue_to_cost_A": g["rates"].sum() / g["cost_A"].sum(),
            "revenue_to_cost_B": g["rates"].sum() / g["cost_B"].sum(),
            "revenue_to_cost_A_shared_by_cv": g["rates"].sum() / g["cost_A_cv"].sum(),
            "share_paying_more_than_cost_A_%": g["net_A"].apply(lambda s: (s > 0).mean() * 100),
        })
        s["net_A_$m"] = s["rates_$m"] - s["cost_A_$m"]
        return s

    by_type = summary(rate, "ptype")
    by_type.to_csv(TABLES / "fiscal_by_property_type.csv", float_format="%.3g")
    print(by_type.round(2).to_string())
    homes = rate[rate["ptype"].isin(["House", "Apartment / unit / flat"])]
    by_sub = summary(homes, "Suburb")
    by_sub[by_sub["units"] >= 100].sort_values("revenue_to_cost_A").to_csv(
        TABLES / "fiscal_homes_by_suburb.csv", float_format="%.3g")

    # Parcel level, per hectare (Urban3's unit of comparison).
    parcels = rate.groupby("wkb").agg(rates=("rates", "sum"), cost_A=("cost_A", "sum"), cost_B=("cost_B", "sum"),
                                      units=("rates", "size"))
    parcels = gpd.GeoDataFrame(parcels, geometry=gpd.GeoSeries.from_wkb(parcels.index.values, crs=2193,
                                                                         index=parcels.index))
    parcels["ha"] = parcels.geometry.area / 1e4
    parcels = parcels[parcels["ha"] > 0.001]
    for k in ["rates", "cost_A", "cost_B"]:
        parcels[f"{k}_per_ha"] = parcels[k] / parcels["ha"]
    parcels["net_A_per_ha"] = parcels["rates_per_ha"] - parcels["cost_A_per_ha"]
    parcels["net_B_per_ha"] = parcels["rates_per_ha"] - parcels["cost_B_per_ha"]

    # Density classes: units per hectare of parcel land.
    rate["units_per_ha"] = rate["wkb"].map(parcels["units"] / parcels["ha"])
    rate["density"] = pd.cut(rate["units_per_ha"], [0, 5, 15, 30, 60, 150, np.inf],
                             labels=["<5", "5–15", "15–30", "30–60", "60–150", "150+"])
    by_density = summary(rate[rate["category"] == "Base"], "density")
    res_parcels = rate[rate["category"] == "Base"].groupby("density", observed=True)["wkb"].unique()
    by_density["net_A_per_ha"] = [parcels.loc[parcels.index.intersection(w), "rates"].sum() / parcels.loc[
        parcels.index.intersection(w), "ha"].sum() - parcels.loc[parcels.index.intersection(w), "cost_A"].sum() /
        parcels.loc[parcels.index.intersection(w), "ha"].sum() for w in res_parcels.reindex(by_density.index)]
    by_density["rates_per_ha"] = [parcels.loc[parcels.index.intersection(w), "rates"].sum() / parcels.loc[
        parcels.index.intersection(w), "ha"].sum() for w in res_parcels.reindex(by_density.index)]
    by_density.to_csv(TABLES / "fiscal_residential_by_density.csv", float_format="%.3g")
    print(by_density.round(2).to_string())

    rate.drop(columns=["geometry", "wkb"]).to_parquet(PROC / "fiscal_units.parquet")
    figures(parcels, by_type, by_density)


def figures(parcels, by_type, by_density):
    xmin, ymin, xmax, ymax = 1741500, 5419800, 1757800, 5443800
    div = LinearSegmentedColormap.from_list(
        "div", ["#a32322", "#e34948", "#f4a3a2", "#f0efec", "#9ec5f4", "#3987e5", "#184f95"])
    blues = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    fig = plt.figure(figsize=(14, 11))
    axes = [fig.add_axes([0.00, 0.01, 0.46, 0.88]), fig.add_axes([0.46, 0.01, 0.46, 0.88])]
    bins = [0, 5_000, 10_000, 20_000, 40_000, 80_000, 200_000, np.inf]
    labels = ["<$5k", "$5–10k", "$10–20k", "$20–40k", "$40–80k", "$80–200k", "$200k+"]
    idx = np.clip(np.digitize(parcels["rates_per_ha"], bins) - 1, 0, len(labels) - 1)
    cols = [blues(i / (len(labels) - 1)) for i in range(len(labels))]
    parcels.plot(ax=axes[0], color=[cols[i] for i in idx], linewidth=0)
    for c, lab in zip(cols, labels):
        axes[0].scatter([], [], marker="s", s=60, color=c, label=lab)
    axes[0].legend(title="Rates per hectare per year", loc="upper left", frameon=False, fontsize=8.5)
    norm = TwoSlopeNorm(vcenter=0, vmin=-40_000, vmax=40_000)
    parcels.plot(ax=axes[1], column="net_A_per_ha", cmap=div, norm=norm, linewidth=0)
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=div), cax=fig.add_axes([0.93, 0.3, 0.015, 0.4]))
    cb.set_label("Rates minus cost of service, $ per ha per year", color=INK2)
    cb.ax.yaxis.set_major_formatter(lambda v, _: f"{v / 1000:+.0f}k")
    cb.outline.set_visible(False)
    for ax, t in zip(axes, ["What land pays: rates per hectare", "Net: pays more (blue) or less (red) than it costs"]):
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_axis_off()
        ax.set_title(t, loc="left", fontsize=11, color=INK2)
    fig.text(0.01, 0.975, "Wellington City: rates vs cost of service, 2025/26 (rates-funded costs)",
             fontsize=14, fontweight="bold", color=INK, va="top")
    fig.savefig(FIGS / "fiscal_map_rates_and_net_per_ha.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    ax = axes[0]
    y = np.arange(len(by_type))
    ax.barh(y - 0.2, by_type["revenue_to_cost_A"], height=0.38, color=SERIES[0], label="Rates-funded cost (A)")
    ax.barh(y + 0.2, by_type["revenue_to_cost_B"], height=0.38, color=SERIES[1],
            label="Full cost incl. depreciation (B)")
    for yy, v in zip(y - 0.2, by_type["revenue_to_cost_A"]):
        ax.text(v + 0.05, yy, f"{v:.2f}", va="center", fontsize=8.5, color=INK2)
    ax.axvline(1, color=INK, lw=1)
    ax.set_yticks(y, list(by_type.index))
    ax.invert_yaxis()
    ax.set_xlabel("Rates paid ÷ cost to serve (1.0 = pays its way)")
    ax.grid(axis="y", visible=False)
    ax.set_title("Rates vs cost, by property type", loc="left", fontsize=11, color=INK2)
    ax.legend(loc="lower right", frameon=False, fontsize=9)

    ax = axes[1]
    y = np.arange(len(by_density))
    ax.barh(y - 0.2, by_density["local_network_cost_A_per_unit"], height=0.38, color=SERIES[0],
            label="Rates-funded (A)")
    ax.barh(y + 0.2, by_density["local_network_cost_B_per_unit"], height=0.38, color=SERIES[1],
            label="Full cost (B)")
    for yy, v in zip(y - 0.2, by_density["local_network_cost_A_per_unit"]):
        ax.text(v + 80, yy, f"${v:,.0f}", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(y, [f"{i} dwellings/ha" for i in by_density.index])
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.set_xlabel("Local roads + pipes cost per dwelling per year")
    ax.grid(axis="y", visible=False)
    ax.set_title("Local infrastructure per home falls with density", loc="left", fontsize=11, color=INK2)
    fig.suptitle("Who pays their way? Wellington City 2025/26", x=0.02, ha="left", fontsize=14,
                 fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(FIGS / "fiscal_revenue_to_cost.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
