"""Roll the fiscal (rates vs cost of service) results up to SA1s and suburbs.

Uses the v2 cost model (fiscal_v2.py: calibrated asset weights, SA1-pooled local network,
demand-based sharing), with v1 (fiscal.py) kept for comparison and the range of rates/cost
ratios across the v2 variants.

Inputs: data/processed/fiscal_units.parquet (fiscal.py), fiscal_units_v2.parquet, units.parquet and
results.parquet (land value rating scenario), data/raw/sa1.geojson + sa1_census2023.geojson.

Outputs
  outputs/blocks/sa1_fiscal.geojson    SA1 polygons (WGS84) with rates, cost, net, land value
                                       and land-value-rating change, for the interactive map
  outputs/tables/fiscal_net_by_suburb.csv
  outputs/figures/fiscal_net_by_suburb.png
"""

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from figures import AXIS, INK, INK2, title  # noqa: E402
from fiscal_v2 import VARIANTS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
TABLES = ROOT / "outputs" / "tables"
BLOCKS = ROOT / "outputs" / "blocks"
FIGS = ROOT / "outputs" / "figures"
BLUE, RED = "#2a78d6", "#e34948"


def load():
    f = pd.read_parquet(PROC / "fiscal_units.parquet")
    v2 = pd.read_parquet(PROC / "fiscal_units_v2.parquet")
    var = [f"cost_A_{n}" for n in VARIANTS]
    f = f.rename(columns={"cost_A": "cost_A_v1", "cost_B": "cost_B_v1", "local_network_A": "local_v1"})
    f = f.merge(v2[var + ["cost_B_v2", "local_A_v2", "residents", "workers"]], left_on="ValuationID",
                right_index=True, how="left")
    f["cost_A"], f["cost_B"], f["local_network_A"] = f["cost_A_v2"], f["cost_B_v2"], f["local_A_v2"]
    geo = gpd.read_parquet(PROC / "units.parquet")[["ValuationID", "geometry"]]
    lv = pd.read_parquet(PROC / "results.parquet", columns=["ValuationID", "rates_cv_3.7", "rates_lv_3.7"])
    f = f.merge(lv, on="ValuationID", how="left")
    f = gpd.GeoDataFrame(f.merge(geo, on="ValuationID"), geometry="geometry", crs=2193)
    f["wkb"] = f.geometry.to_wkb()
    f["parcel_m2"] = f.geometry.area / f.groupby("wkb")["ValuationID"].transform("count")
    f["home"] = f["ptype"].isin(["House", "Apartment / unit / flat"])
    return f


def sa1_summary(f):
    sa1 = gpd.read_file(RAW / "sa1.geojson").to_crs(2193)
    sa1 = sa1[sa1["LAND_AREA_SQ_KM"] > 0]
    census = gpd.read_file(RAW / "sa1_census2023.geojson", ignore_geometry=True)
    pop = pd.to_numeric(census.set_index("SA12023_V1_00")["VAR_1_3"], errors="coerce").where(lambda v: v >= 0)
    pts = gpd.GeoDataFrame(geometry=f.geometry.representative_point(), index=f.index, crs=2193)
    j = gpd.sjoin(pts, sa1[["SA12025_V1_00", "geometry"]], predicate="within")
    f = f.join(j["SA12025_V1_00"])
    g = f.groupby("SA12025_V1_00")
    s = pd.DataFrame({
        "suburb": g["Suburb"].agg(lambda x: x.mode().iat[0] if len(x.mode()) else ""),
        "units": g.size(),
        "homes": g["home"].sum(),
        "land_m2": g["parcel_m2"].sum(),
        "land_value": g["LandValue"].sum(),
        "rates": g["rates"].sum(),
        "cost_a": g["cost_A"].sum(),
        "cost_b": g["cost_B"].sum(),
        "local_net": g["local_network_A"].sum(),
        "cost_v1": g["cost_A_v1"].sum(),
        "workers": g["workers"].sum(),
        **{f"var_{n}": g[f"cost_A_{n}"].sum() for n in VARIANTS},
        "gr_cv": g["rates_cv_3.7"].sum(),
        "gr_lv": g["rates_lv_3.7"].sum(),
    })
    hg = f[f["home"]].groupby("SA12025_V1_00")
    s["local_net_per_home"] = hg["local_network_A"].mean()
    s["residents"] = s.index.map(pop)
    ha = s["land_m2"] / 1e4
    out = pd.DataFrame(index=s.index)
    out["suburb"] = s["suburb"]
    out["units"] = s["units"].astype(int)
    out["residents"] = s["residents"]
    out["homes_per_ha"] = (s["homes"] / ha).round(1)
    out["rates"] = s["rates"].round(0)
    out["cost"] = s["cost_a"].round(0)
    out["cost_full"] = s["cost_b"].round(0)
    out["net"] = (s["rates"] - s["cost_a"]).round(0)
    out["net_full"] = (s["rates"] - s["cost_b"]).round(0)
    out["rates_ha"] = (s["rates"] / ha).round(0)
    out["cost_ha"] = (s["cost_a"] / ha).round(0)
    out["net_ha"] = ((s["rates"] - s["cost_a"]) / ha).round(0)
    out["net_full_ha"] = ((s["rates"] - s["cost_b"]) / ha).round(0)
    out["ha"] = ha.round(2)
    out["ratio"] = (s["rates"] / s["cost_a"]).round(2)
    out["lv_m2"] = (s["land_value"] / s["land_m2"]).round(0)
    out["local_home"] = s["local_net_per_home"].round(0)
    out["workers"] = s["workers"].round(0)
    out["people_ha"] = ((s["residents"].fillna(0) + s["workers"]) / ha).round(0)
    out["net_v1"] = (s["rates"] - s["cost_v1"]).round(0)
    out["net_v1_ha"] = ((s["rates"] - s["cost_v1"]) / ha).round(0)
    ratios = pd.concat([s["rates"] / s[f"var_{n}"] for n in VARIANTS], axis=1)
    out["ratio_lo"], out["ratio_hi"] = ratios.min(axis=1).round(2), ratios.max(axis=1).round(2)
    out["lvr_change_pct"] = ((s["gr_lv"] / s["gr_cv"] - 1) * 100).round(1)
    shapes = sa1.set_index("SA12025_V1_00")[["geometry"]].join(out, how="inner")
    shapes.index.name = "sa1"
    shapes["geometry"] = shapes.geometry.simplify(4)
    shapes = shapes.reset_index().to_crs(4326)
    shapes.to_file(BLOCKS / "sa1_fiscal.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
    print(f"SA1s: {len(shapes)}, file {(BLOCKS / 'sa1_fiscal.geojson').stat().st_size / 1e6:.1f} MB")
    return f


def suburb_summary(f):
    g = f.groupby("Suburb")
    s = pd.DataFrame({
        "units": g.size(),
        "rates_$m": g["rates"].sum() / 1e6,
        "cost_$m": g["cost_A"].sum() / 1e6,
        "cost_full_$m": g["cost_B"].sum() / 1e6,
        "cost_v1_$m": g["cost_A_v1"].sum() / 1e6,
        **{f"var_{n}": g[f"cost_A_{n}"].sum() / 1e6 for n in VARIANTS},
        "commercial_rates_$m": f[f["category"] == "Commercial"].groupby("Suburb")["rates"].sum().reindex(
            g.size().index).fillna(0) / 1e6,
    })
    s["net_$m"] = s["rates_$m"] - s["cost_$m"]
    s["net_full_$m"] = s["rates_$m"] - s["cost_full_$m"]
    s["ratio"] = s["rates_$m"] / s["cost_$m"]
    s["net_v1_$m"] = s["rates_$m"] - s["cost_v1_$m"]
    nets = pd.concat([s["rates_$m"] - s[f"var_{n}"] for n in VARIANTS], axis=1)
    s["net_lo_$m"], s["net_hi_$m"] = nets.min(axis=1), nets.max(axis=1)
    s = s.drop(columns=[f"var_{n}" for n in VARIANTS])
    s = s[s["units"] >= 50].sort_values("net_$m")
    s.to_csv(TABLES / "fiscal_net_by_suburb.csv", float_format="%.3f")
    return s


def fig_net(s):
    fig, ax = plt.subplots(figsize=(9, 13))
    y = np.arange(len(s))
    v = s["net_$m"].values
    ax.barh(y, v, color=np.where(v >= 0, BLUE, RED), height=0.72)
    ax.errorbar(v, y, xerr=[v - s["net_lo_$m"].values, s["net_hi_$m"].values - v], fmt="none", ecolor=INK,
                elinewidth=0.8, capsize=2)
    ax.axvline(0, color=AXIS, lw=1)
    for yy, val in zip(y, v):
        edge = s["net_hi_$m"].iat[int(yy)] if val >= 0 else s["net_lo_$m"].iat[int(yy)]
        ax.text(edge + (1.2 if val >= 0 else -1.2), yy, f"{val:+.1f}", va="center",
                ha="left" if val >= 0 else "right", fontsize=7.5, color=INK2)
    ax.set_yticks(y, s.index, fontsize=8)
    ax.set_ylim(-0.7, len(s) - 0.3)
    ax.xaxis.set_major_formatter(lambda x, _: ("+" if x > 0 else "−" if x < 0 else "") + f"${abs(x):.0f}m")
    ax.set_xlabel("Rates paid minus cost of service, $m per year (2025/26, excl. GST)")
    ax.grid(axis="y", visible=False)
    lo, hi = v.min(), v.max()
    ax.set_xlim(lo - 12, hi + 14)
    title(ax, "Wellington City: which suburbs pay more than they cost?",
          "Blue pays more than its rates-funded cost, red less (cost model v2; whiskers: range\n"
          "across model variants). Suburbs with 50+ rating units.")
    fig.tight_layout()
    fig.savefig(FIGS / "fiscal_net_by_suburb.png", dpi=160)
    plt.close(fig)


def main():
    f = load()
    f = sa1_summary(f)
    s = suburb_summary(f)
    fig_net(s)
    print(s.round(1).head(8).to_string())
    print(s.round(1).tail(8).to_string())


if __name__ == "__main__":
    main()
