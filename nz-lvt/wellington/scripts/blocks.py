"""Land value and land value rates by block, and a test of "block-rate" land valuation.

Two questions:

1. Aggregation: how much land value (and general-rate revenue) sits in each block of
   the city, per m2 of private land and per hectare of block?
2. Block-rate valuation (Doucet's "simplest viable method", cf. Qingdao's flat rate per
   tax district): if every parcel in a block got the same land rate per m2, how close
   would that be to the official parcel land values? Scored with IAAO ratio-study
   statistics, using a leave-one-out block rate so a parcel never sets its own value.

Block schemes: square grids (100 m, 250 m, 500 m, 1 km), Stats NZ SA1s and SA2s, and
"value districts" = SA2 x district plan zone (the closest analogue to a
Korean/Qingdao-style district of similar land).

Two block-rate methods:
  flat      value = block geometric-mean $/m2 x parcel area
  size_adj  as flat, plus one citywide adjustment for lot size (land $/m2 falls as
            lots get bigger), estimated within blocks: log($/m2) ~ block + b*log(area)

Outputs: outputs/tables/blocks_*.csv, outputs/blocks/*.geojson, figures in outputs/figures/.
"""

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import box  # noqa: E402

from figures import BLUE_RAMP, GRID, INK2, MUTED, SERIES, title  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "processed" / "results.parquet"
SA2 = ROOT / "data" / "raw" / "sa2.geojson"
SA1 = ROOT / "data" / "raw" / "sa1.geojson"
SA1_CENSUS = ROOT / "data" / "raw" / "sa1_census2023.geojson"
TABLES = ROOT / "outputs" / "tables"
BLOCKS = ROOT / "outputs" / "blocks"
FIGS = ROOT / "outputs" / "figures"

GRID_SIZES = [100, 250, 500, 1000]
URBAN_MAX_AREA = 20_000  # parcels > 2 ha (rural, large institutional) are excluded from the ratio study
MIN_PARCELS = 5  # blocks with fewer parcels are too thin to set a rate
SA1_CONTEXT_M2 = 500_000  # SA1s > 50 ha (rural, reserves) drawn as grey context on maps
NOMINAL_LV = 50_000  # access strips, slivers, nominal-value lots: excluded from the ratio study
IQR_FENCE = 3.0  # IAAO-style outlier trim on ratios (3x interquartile range)


def parcels():
    """One row per land parcel: rating units that share a parcel polygon are summed."""
    u = gpd.read_parquet(RESULTS)
    u["wkb"] = u.geometry.to_wkb()
    agg = {
        "LandValue": "sum",
        "CapitalValue": "sum",
        "rates_cv_3.7": "sum",
        "rates_lv_3.7": "sum",
        "zone": "first",
        "Suburb": "first",
        "ValuationID": "count",
        "geometry": "first",
    }
    p = u.groupby("wkb").agg(agg).rename(columns={"ValuationID": "units"})
    p["commercial"] = u.groupby("wkb")["category"].agg(lambda s: (s == "Commercial").any())
    base = u["category"] == "Base"
    for col, src in [("res_rates_now", "rates_cv_3.7"), ("res_rates_lv", "rates_lv_3.7"), ("res_land_value", "LandValue")]:
        p[col] = u[src].where(base, 0).groupby(u["wkb"]).sum()
    p = gpd.GeoDataFrame(p.reset_index(drop=True), geometry="geometry", crs=u.crs)
    p["area_m2"] = p.geometry.area
    p = p[(p["area_m2"] > 20) & (p["LandValue"] > 0)].copy()
    p["lv_m2"] = p["LandValue"] / p["area_m2"]
    p["urban"] = (p["area_m2"] <= URBAN_MAX_AREA) & (p["zone"] != "GRUZ")
    p["study"] = p["urban"] & (p["LandValue"] >= NOMINAL_LV)
    return p


def square_grid(bounds, size):
    x0, y0, x1, y1 = bounds
    xs = np.arange(np.floor(x0 / size) * size, x1 + size, size)
    ys = np.arange(np.floor(y0 / size) * size, y1 + size, size)
    cells = [box(x, y, x + size, y + size) for x in xs for y in ys]
    ids = [f"g{size}_{int(x)}_{int(y)}" for x in xs for y in ys]
    return gpd.GeoDataFrame({"block_id": ids}, geometry=cells, crs=2193)


def load_sa1(sa2):
    """SA1 2025 blocks inside Wellington City, with 2023 census usually resident population."""
    sa1 = gpd.read_file(SA1).to_crs(2193)
    sa1 = sa1[sa1["LAND_AREA_SQ_KM"] > 0]
    pts = gpd.GeoDataFrame(geometry=sa1.geometry.representative_point(), index=sa1.index, crs=sa1.crs)
    inside = gpd.sjoin(pts, sa2[["geometry"]], predicate="within").index.unique()
    sa1 = sa1.loc[inside]
    census = gpd.read_file(SA1_CENSUS, ignore_geometry=True)
    pop = census.set_index("SA12023_V1_00")["VAR_1_3"]
    pop = pd.to_numeric(pop, errors="coerce").where(lambda v: v >= 0)  # Stats NZ suppression codes are negative
    sa1["residents_2023"] = sa1["SA12025_V1_00"].map(pop)
    sa1["block_id"] = "sa1_" + sa1["SA12025_V1_00"].astype(str)
    return sa1[["block_id", "residents_2023", "geometry"]]


def assign(p, blocks):
    pts = gpd.GeoDataFrame(geometry=p.geometry.representative_point(), index=p.index, crs=p.crs)
    j = gpd.sjoin(pts, blocks[["block_id", "geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    return j["block_id"]


def loo_mean(values, groups):
    """Leave-one-out group mean; NaN where the group has fewer than MIN_PARCELS members."""
    g = values.groupby(groups)
    s, n = g.transform("sum"), g.transform("count")
    out = (s - values) / (n - 1)
    return out.where(n >= MIN_PARCELS)


def block_values(p, block):
    """Predicted land value for each parcel under the flat and size-adjusted block rates."""
    logr = np.log(p["lv_m2"])
    loga = np.log(p["area_m2"])
    flat = np.exp(loo_mean(logr, block)) * p["area_m2"]

    # Within-block slope of log($/m2) on log(area), pooled citywide on urban parcels.
    m = p["study"] & block.notna()
    dr = logr[m] - logr[m].groupby(block[m]).transform("mean")
    da = loga[m] - loga[m].groupby(block[m]).transform("mean")
    beta = float((dr * da).sum() / (da * da).sum())
    adj_logr = loo_mean(logr - beta * loga, block) + beta * loga
    size_adj = np.exp(adj_logr) * p["area_m2"]
    return flat, size_adj, beta


def ratio_stats(pred, actual):
    r = (pred / actual).dropna()
    r = r[np.isfinite(r)]
    q1, q3 = r.quantile([0.25, 0.75])
    keep = r.between(q1 - IQR_FENCE * (q3 - q1), q3 + IQR_FENCE * (q3 - q1))
    trimmed = (~keep).mean() * 100
    r = r[keep]
    med = r.median()
    a = actual.loc[r.index]
    return {
        "parcels_valued": len(r),
        "trimmed_%": trimmed,
        "median_ratio": med,
        "COD": (r - med).abs().mean() / med * 100,
        "PRD": r.mean() / ((pred.loc[r.index]).sum() / a.sum()),
        "within_10pct_%": ((r / med - 1).abs() <= 0.10).mean() * 100,
        "within_20pct_%": ((r / med - 1).abs() <= 0.20).mean() * 100,
    }


def block_summary(p, block, blocks_gdf):
    g = p.groupby(block)
    s = pd.DataFrame({
        "parcels": g.size(),
        "rating_units": g["units"].sum(),
        "parcel_land_m2": g["area_m2"].sum(),
        "land_value": g["LandValue"].sum(),
        "capital_value": g["CapitalValue"].sum(),
        "rates_now": g["rates_cv_3.7"].sum(),
        "rates_lv": g["rates_lv_3.7"].sum(),
        "median_parcel_lv_m2": g["lv_m2"].median(),
        "res_land_value": g["res_land_value"].sum(),
        "res_rates_now": g["res_rates_now"].sum(),
        "res_rates_lv": g["res_rates_lv"].sum(),
    })
    s["res_rates_change_%"] = (s["res_rates_lv"] / s["res_rates_now"] - 1) * 100
    s["lv_per_m2_land"] = s["land_value"] / s["parcel_land_m2"]
    s["land_share_of_cv"] = s["land_value"] / s["capital_value"]
    out = blocks_gdf.set_index("block_id").join(s, how="inner")
    gross_ha = out.geometry.area / 10_000
    out["lv_per_ha_block"] = out["land_value"] / gross_ha
    out["rates_now_per_ha_block"] = out["rates_now"] / gross_ha
    out["rates_lv_per_ha_block"] = out["rates_lv"] / gross_ha
    out["rates_lv_per_m2_land"] = out["rates_lv"] / out["parcel_land_m2"]
    out["rates_change_%"] = (out["rates_lv"] / out["rates_now"] - 1) * 100
    if "residents_2023" in out:
        people = out["residents_2023"].where(out["residents_2023"] > 0)
        out["land_value_per_resident"] = out["land_value"] / people
        out["res_rates_now_per_resident"] = out["res_rates_now"] / people
        out["res_rates_lv_per_resident"] = out["res_rates_lv"] / people
    return out.reset_index()


def rates_effect(p, pred, block_name, method):
    """Bills if block-rate values replaced official land values (revenue-neutral, 3.7x kept)."""
    m = pred.notna()
    d = np.where(p.loc[m, "commercial"], 3.7, 1.0)
    total = p.loc[m, "rates_lv_3.7"].sum()
    w = pred[m] * d
    bill = total * w / w.sum()
    diff = bill / p.loc[m, "rates_lv_3.7"] - 1
    return {
        "blocks": block_name,
        "method": method,
        "median_abs_bill_diff_%": diff.abs().median() * 100,
        "bills_within_10pct_%": (diff.abs() <= 0.10).mean() * 100,
        "bills_within_20pct_%": (diff.abs() <= 0.20).mean() * 100,
    }


def main():
    TABLES.mkdir(parents=True, exist_ok=True)
    BLOCKS.mkdir(parents=True, exist_ok=True)
    p = parcels()
    print(f"{len(p):,} parcels ({p['urban'].sum():,} urban <= 2 ha), "
          f"LV ${p['LandValue'].sum() / 1e9:.1f}bn")

    sa2 = gpd.read_file(SA2).to_crs(2193)
    sa2["block_id"] = "sa2_" + sa2["sa2_code"].astype(str) + " " + sa2["sa2_name"]
    schemes = {f"grid {s} m": square_grid(p.total_bounds, s) for s in GRID_SIZES}
    schemes["SA2"] = sa2[["block_id", "geometry"]]
    schemes["SA1"] = load_sa1(sa2)

    assignments = {name: assign(p, gdf) for name, gdf in schemes.items()}
    assignments["SA2 x zone"] = assignments["SA2"] + " | " + p["zone"]

    acc_rows, bill_rows = [], []
    urban = p["study"]
    for name, block in assignments.items():
        flat, size_adj, beta = block_values(p, block)
        for method, pred in [("flat", flat), ("size_adj", size_adj)]:
            row = {"blocks": name, "method": method, "size_slope": beta if method == "size_adj" else np.nan}
            n_blocks = block[urban].map(block[urban].value_counts()).ge(MIN_PARCELS)
            row["blocks_used"] = block[urban][n_blocks].nunique()
            row.update(ratio_stats(pred[urban], p.loc[urban, "LandValue"]))
            row["coverage_%"] = row["parcels_valued"] / urban.sum() * 100
            acc_rows.append(row)
            bill_rows.append(rates_effect(p[urban], pred[urban], name, method))

    acc = pd.DataFrame(acc_rows)
    bills = pd.DataFrame(bill_rows)
    acc = acc.merge(bills, on=["blocks", "method"])
    acc.to_csv(TABLES / "blocks_valuation_accuracy.csv", index=False, float_format="%.4g")
    print(acc.round(2).to_string(index=False))

    # Aggregated block tables / GeoJSON for mapping.
    cols = ["block_id", "parcels", "rating_units", "parcel_land_m2", "land_value", "lv_per_m2_land",
            "lv_per_ha_block", "land_share_of_cv", "rates_now", "rates_lv", "rates_change_%",
            "rates_lv_per_m2_land", "rates_lv_per_ha_block", "res_rates_now", "res_rates_lv",
            "res_rates_change_%"]
    for name, fname in [("grid 250 m", "grid_250m"), ("grid 500 m", "grid_500m"), ("SA2", "sa2"), ("SA1", "sa1")]:
        s = block_summary(p, assignments[name], schemes[name])
        s.to_crs(4326).to_file(BLOCKS / f"{fname}.geojson", driver="GeoJSON", COORDINATE_PRECISION=6)
        if name in ("SA2", "SA1"):
            extra = ["residents_2023", "land_value_per_resident", "res_rates_now_per_resident",
                     "res_rates_lv_per_resident"] if name == "SA1" else []
            s.sort_values("lv_per_m2_land", ascending=False)[cols + extra].to_csv(
                TABLES / f"blocks_{fname}.csv", index=False, float_format="%.4g")
        if name == "SA1":
            fig_sa1_maps(s)
            n = s["parcels"]
            print(f"SA1: {len(s)} blocks, median {n.median():.0f} parcels, "
                  f"median residents {s['residents_2023'].median():.0f}, "
                  f"census matched {s['residents_2023'].notna().mean():.0%}")
    grid250 = block_summary(p, assignments["grid 250 m"], schemes["grid 250 m"])
    fig_block_map(grid250)
    fig_accuracy(acc)


def fig_block_map(g):
    xmin, ymin, xmax, ymax = 1741500, 5419800, 1757800, 5443800
    g = g[g["parcel_land_m2"] >= 5_000]  # skip cells with barely any private land
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    bins = [0, 1_000_000, 2_500_000, 5_000_000, 7_500_000, 10_000_000, 20_000_000, np.inf]
    labels = ["<$1m", "$1–2.5m", "$2.5–5m", "$5–7.5m", "$7.5–10m", "$10–20m", "$20m+"]
    idx = np.clip(np.digitize(g["lv_per_ha_block"], bins) - 1, 0, len(labels) - 1)
    fig, ax = plt.subplots(figsize=(8, 11.5))
    g.plot(ax=ax, color=[cmap(i / (len(labels) - 1)) for i in idx], edgecolor="#fcfcfb", linewidth=0.3)
    for i, lab in enumerate(labels):
        ax.scatter([], [], marker="s", s=60, color=cmap(i / (len(labels) - 1)), label=lab)
    ax.legend(title="Land value per hectare", loc="upper left", frameon=False, fontsize=8.5, title_fontsize=9)
    ax.set_axis_off()
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    title(ax, "Wellington land value in 250 m blocks",
          "Rateable land value per hectare of block (gross, incl. roads). Blocks with <0.5 ha private land hidden.")
    fig.tight_layout()
    fig.savefig(FIGS / "map_blocks_250m_land_value_per_ha.png", dpi=160)
    plt.close(fig)


def fig_sa1_maps(s):
    xmin, ymin, xmax, ymax = 1741500, 5419800, 1757800, 5443800
    s = s[s["parcel_land_m2"] >= 2_000]
    big = s.geometry.area > SA1_CONTEXT_M2
    context, s = s[big], s[~big]

    fig, ax = plt.subplots(figsize=(8, 11.5))
    context.plot(ax=ax, color=GRID, edgecolor="#fcfcfb", linewidth=0.3)
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    bins = [0, 250, 500, 1000, 1500, 2500, 5000, np.inf]
    labels = ["<$250", "$250–500", "$500–1k", "$1–1.5k", "$1.5–2.5k", "$2.5–5k", "$5k+"]
    idx = np.clip(np.digitize(s["lv_per_m2_land"], bins) - 1, 0, len(labels) - 1)
    s.plot(ax=ax, color=[cmap(i / (len(labels) - 1)) for i in idx], edgecolor="#fcfcfb", linewidth=0.3)
    for i, lab in enumerate(labels):
        ax.scatter([], [], marker="s", s=60, color=cmap(i / (len(labels) - 1)), label=lab)
    ax.scatter([], [], marker="s", s=60, color=GRID, label="SA1 > 50 ha (not shaded)")
    ax.legend(title="Land value per m² of private land", loc="upper left", frameon=False,
              fontsize=8.5, title_fontsize=9)
    ax.set_axis_off()
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    title(ax, "Wellington land value by SA1", "Stats NZ Statistical Area 1 (2025), rateable land, 2024 valuations")
    fig.tight_layout()
    fig.savefig(FIGS / "map_sa1_land_value_per_m2.png", dpi=160)
    plt.close(fig)

    from figures import DIVERGING  # noqa: E402
    from matplotlib.colors import TwoSlopeNorm  # noqa: E402

    r = s[s["res_rates_now"] > 0]
    fig, ax = plt.subplots(figsize=(8, 11.5))
    context.plot(ax=ax, color=GRID, edgecolor="#fcfcfb", linewidth=0.3)
    norm = TwoSlopeNorm(vcenter=0, vmin=-40, vmax=40)
    r.plot(ax=ax, column="res_rates_change_%", cmap=DIVERGING, norm=norm, edgecolor="#fcfcfb", linewidth=0.3)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=DIVERGING)
    cb = fig.colorbar(sm, ax=ax, shrink=0.45, pad=0.01)
    cb.set_label("Change in residential general rates", color=INK2)
    cb.ax.yaxis.set_major_formatter(lambda x, _: f"{x:+.0f}%")
    cb.outline.set_visible(False)
    ax.set_axis_off()
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    title(ax, "Residential rates change by SA1, capital → land value",
          "Revenue-neutral, 3.7× differential kept; blue pays less, red more; grey = SA1 > 50 ha")
    fig.tight_layout()
    fig.savefig(FIGS / "map_sa1_residential_rates_change.png", dpi=160)
    plt.close(fig)


def fig_accuracy(acc):
    order = ["grid 100 m", "SA1", "SA2 x zone", "grid 250 m", "grid 500 m", "SA2", "grid 1000 m"]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    y = np.arange(len(order))
    h = 0.36
    for i, (method, lab) in enumerate([("flat", "Flat $/m² per block"),
                                       ("size_adj", "Block $/m² + citywide lot-size adjustment")]):
        a = acc[acc["method"] == method].set_index("blocks").loc[order]
        yy = y[::-1] + (h / 2 if i == 0 else -h / 2)
        ax.barh(yy, a["within_20pct_%"], height=h - 0.04, color=SERIES[i], label=lab)
        for v, yv, cod in zip(a["within_20pct_%"], yy, a["COD"]):
            ax.text(v + 1, yv, f"{v:.0f}%  (COD {cod:.0f})", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(y[::-1], order)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax.set_xlabel("Urban parcels valued within ±20% of their official land value")
    ax.grid(axis="y", visible=False)
    title(ax, "How coarse can land valuation blocks be?",
          "Leave-one-out block rate vs official 2024 land value; IAAO COD target ≤15 (residential)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.4, -0.16), frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGS / "blocks_valuation_accuracy.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
