"""Static charts for the Wellington land value rating analysis (outputs/figures/*.png)."""

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "processed" / "results.parquet"
TABLES = ROOT / "outputs" / "tables"
FIGS = ROOT / "outputs" / "figures"
BIG_PARCEL_M2 = 20_000

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834"]
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGING = LinearSegmentedColormap.from_list(
    "div", ["#184f95", "#3987e5", "#9ec5f4", "#f0efec", "#f4a3a2", "#e34948", "#a32322"]
)

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.facecolor": SURFACE,
    "figure.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK2,
    "xtick.color": MUTED,
    "ytick.color": INK2,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
})


def title(ax, text, sub):
    kw = dict(xy=(0, 1), xycoords="axes fraction", textcoords="offset points", ha="left", va="bottom")
    ax.annotate(text, xytext=(0, 26), fontsize=13, color=INK, fontweight="bold", **kw)
    ax.annotate(sub, xytext=(0, 10), fontsize=9.5, color=INK2, **kw)


def fig_by_type():
    a = pd.read_csv(TABLES / "by_type_lv_3.7.csv", index_col=0)
    b = pd.read_csv(TABLES / "by_type_lv_share.csv", index_col=0)
    order = a.index[::-1]
    y = np.arange(len(order))
    h = 0.36
    fig, ax = plt.subplots(figsize=(9, 5.6))
    for i, (df, lab) in enumerate([
        (a, "Land value, keep 3.7× commercial differential"),
        (b, "Land value, residential/commercial revenue split held"),
    ]):
        vals = df.loc[order, "median_change_%"].values
        ax.barh(y + (h / 2 if i == 0 else -h / 2), vals, height=h - 0.04, color=SERIES[i], label=lab)
        for yy, v in zip(y + (h / 2 if i == 0 else -h / 2), vals):
            ax.text(v + (2 if v >= 0 else -2), yy, f"{v:+.0f}%".replace("-0%", "0%").replace("+0%", "0%"), va="center",
                    ha="left" if v >= 0 else "right", fontsize=8.5, color=INK2)
    ax.axvline(0, color=AXIS, lw=1)
    ax.set_yticks(y, [f"{t}  (n={a.loc[t, 'units']:,.0f})" for t in order])
    ax.set_xlabel("Median change in general-rate bill vs today (capital value, 3.7×)")
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")
    ax.set_xlim(-40, 150)
    ax.grid(axis="y", visible=False)
    title(ax, "Who pays more under land value rating?",
          "Wellington City general rate, revenue-neutral, 2024 valuations")
    ax.legend(loc="upper center", bbox_to_anchor=(0.35, -0.13), ncol=1, frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGS / "change_by_property_type.png", dpi=160)
    plt.close(fig)


def fig_suburbs():
    s = pd.read_csv(TABLES / "homes_by_suburb_lv_3.7.csv", index_col=0).sort_values("median_change_%")
    fig, ax = plt.subplots(figsize=(8, 11))
    v = s["median_change_%"].values
    norm = TwoSlopeNorm(vcenter=0, vmin=-35, vmax=35)
    ax.barh(np.arange(len(s)), v, color=DIVERGING(norm(v)), height=0.72)
    ax.axvline(0, color=AXIS, lw=1)
    ax.set_yticks(np.arange(len(s)), s.index, fontsize=8.5)
    ax.set_ylim(-0.7, len(s) - 0.3)
    ax.xaxis.set_major_formatter(lambda x, _: f"{x:+.0f}%")
    ax.set_xlabel("Median change in homeowner general-rate bill (houses + apartments)")
    ax.grid(axis="y", visible=False)
    for i, (val, ls) in enumerate(zip(v, s["land_share_of_cv"])):
        ax.text(38 if val < 0 else -38, i, f"land {ls:.0%}", va="center",
                ha="right" if val < 0 else "left", fontsize=7.5, color=MUTED)
    ax.set_xlim(-40, 45)
    title(ax, "Suburbs: land-rich areas pay more, apartments less",
          "Land value rating, 3.7× commercial differential kept. Suburbs with ≥100 homes.")
    fig.tight_layout()
    fig.savefig(FIGS / "change_by_suburb.png", dpi=160)
    plt.close(fig)


def load_parcels():
    u = gpd.read_parquet(RESULTS)
    u["land_area"] = pd.to_numeric(u["LandArea"], errors="coerce")
    # Units share their parent parcel polygon: aggregate to one polygon per parcel.
    g = u.dissolve(
        by="parcel_id",
        aggfunc={"LandValue": "sum", "rates_cv_3.7": "sum", "rates_lv_3.7": "sum", "land_area": "max"},
    )
    area = g["land_area"].where(g["land_area"] > 0, g.geometry.area)
    g["lv_m2"] = g["LandValue"] / area
    g["pct"] = (g["rates_lv_3.7"] / g["rates_cv_3.7"] - 1) * 100
    return g


def fig_maps(g):
    # Urban extent; large rural and reserve-sized parcels are drawn as neutral context
    # because their colour would dominate the map without representing many people.
    xmin, ymin, xmax, ymax = 1741500, 5419800, 1757800, 5443800
    g = g.cx[xmin:xmax, ymin:ymax]
    big = g.geometry.area > BIG_PARCEL_M2
    context, g = g[big], g[~big]

    fig, ax = plt.subplots(figsize=(8, 11.5))
    context.plot(ax=ax, color=GRID, linewidth=0)
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    bins = [0, 250, 500, 1000, 1500, 2500, 5000, np.inf]
    labels = ["<$250", "$250–500", "$500–1k", "$1–1.5k", "$1.5–2.5k", "$2.5–5k", "$5k+"]
    idx = np.clip(np.digitize(g["lv_m2"], bins) - 1, 0, len(labels) - 1)
    g.plot(ax=ax, color=[cmap(i / (len(labels) - 1)) for i in idx], linewidth=0)
    for i, lab in enumerate(labels):
        ax.scatter([], [], marker="s", s=60, color=cmap(i / (len(labels) - 1)), label=lab)
    ax.scatter([], [], marker="s", s=60, color=GRID, label="Parcel > 2 ha (not shaded)")
    ax.legend(title="Land value per m²", loc="upper left", frameon=False, fontsize=8.5, title_fontsize=9)
    ax.set_axis_off()
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    title(ax, "Where Wellington's land value sits", "Rating land value per m² of parcel, 2024 revaluation")
    fig.tight_layout()
    fig.savefig(FIGS / "map_land_value_per_m2.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 11.5))
    context.plot(ax=ax, color=GRID, linewidth=0)
    norm = TwoSlopeNorm(vcenter=0, vmin=-50, vmax=100)
    g.plot(ax=ax, column="pct", cmap=DIVERGING, norm=norm, linewidth=0)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=DIVERGING)
    cb = fig.colorbar(sm, ax=ax, shrink=0.45, pad=0.01)
    cb.set_label("Change in general-rate bill", color=INK2)
    cb.ax.yaxis.set_major_formatter(lambda x, _: f"{x:+.0f}%")
    cb.outline.set_visible(False)
    ax.set_axis_off()
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    title(ax, "Change in rates bill, capital → land value",
          "Revenue-neutral, 3.7× differential kept; blue pays less, red more; grey = parcel > 2 ha")
    fig.tight_layout()
    fig.savefig(FIGS / "map_rates_change.png", dpi=160)
    plt.close(fig)


def main():
    FIGS.mkdir(parents=True, exist_ok=True)
    fig_by_type()
    fig_suburbs()
    fig_maps(load_parcels())
    print("figures written to", FIGS)


if __name__ == "__main__":
    main()
