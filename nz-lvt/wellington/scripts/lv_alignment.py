"""Does land value rating bring each property's rates closer to its cost of service?

Takes the 2025/26 rates bills and cost-of-service estimates from fiscal.py and re-levies
the same total rates under alternative designs (all revenue-neutral, excl. GST):

  cv_today        current system: value-based rates on capital value, fixed water and
                  sewerage charges for Base properties ($490.44), downtown levy on CV.
  lv_same_split   all value-based rates moved to land value; Base and Commercial each
                  raise what they raise today.
  lv_loading      land value with today's commercial loading (the ratio of commercial to
                  Base value-based rates per $ of CV, 3.4x in 2025/26), applied per $ of LV.
  lv_2x           land value, commercial loading cut to 2x.
  lv_flat         land value, no commercial loading.
  lv_uagc         land value with today's loading, plus a uniform annual general charge
                  per rating unit so fixed charges reach the legal cap of 30% of rates
                  (Local Government (Rating) Act 2002, s21).
The downtown levy stays on downtown commercial property (moved to land value in LV
scenarios).

Alignment is measured against two cost allocations (fiscal.py):
  cost_A     shared costs split per rating unit;
  cost_A_cv  shared costs split by capital value.
Metrics: the cross-subsidy (sum of rates paid above cost, which equals the sum paid
below cost), the share of units within +/-25% of cost, rates/cost by property type and
the suburb-level cross-subsidy.

Writes outputs/tables/lv_alignment_*.csv and outputs/figures/lv_alignment.png.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from figures import AXIS, INK, INK2, SERIES, title  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
TABLES = ROOT / "outputs" / "tables"
FIGS = ROOT / "outputs" / "figures"

BASE_FIXED = 337.76 + 152.68
DOWNTOWN = 0.00172428
UAGC_CAP = 0.30
LABELS = {
    "cv_today": "Today (capital value)",
    "lv_same_split": "Land value, same res/comm split",
    "lv_loading": "Land value, today's commercial loading",
    "lv_2x": "Land value, commercial 2×",
    "lv_flat": "Land value, no commercial loading",
    "lv_uagc": "Land value + max fixed charge",
}
TYPES = ["House", "Apartment / unit / flat", "Residential vacant land", "Commercial",
         "Commercial land, little or no building"]


def scenarios(f):
    base = f["category"] == "Base"
    fixed = np.where(base, BASE_FIXED, 0.0)
    dt_mask = f["downtown"] & ~base
    dt = np.where(dt_mask, DOWNTOWN * f["CapitalValue"], 0.0)
    value = f["rates"] - fixed - dt
    total, value_total, dt_total = f["rates"].sum(), value.sum(), dt.sum()
    lv, cv = f["LandValue"], f["CapitalValue"]
    dt_lv = np.where(dt_mask, dt_total * lv / lv[dt_mask].sum(), 0.0)
    loading = (value[~base].sum() / cv[~base].sum()) / (value[base].sum() / cv[base].sum())

    def on_lv(d, amount):
        w = lv * np.where(base, 1.0, d)
        return amount * w / w.sum()

    out = {"cv_today": f["rates"]}
    same = pd.Series(0.0, index=f.index)
    for m in (base, ~base):
        same[m] = value[m].sum() * lv[m] / lv[m].sum()
    out["lv_same_split"] = fixed + same + dt_lv
    out["lv_loading"] = fixed + on_lv(loading, value_total) + dt_lv
    out["lv_2x"] = fixed + on_lv(2.0, value_total) + dt_lv
    out["lv_flat"] = fixed + on_lv(1.0, value_total) + dt_lv
    uagc = (UAGC_CAP * total - fixed.sum()) / len(f)
    out["lv_uagc"] = fixed + uagc + on_lv(loading, value_total - uagc * len(f)) + dt_lv
    for k, v in out.items():
        assert abs(v.sum() - total) < 1, k
    return pd.DataFrame(out), loading, uagc


def metrics(f, bills, cost_col):
    rows = []
    cost = f[cost_col]
    for s in bills:
        r = bills[s]
        net = r - cost
        sub = net.groupby(f["Suburb"]).sum()
        row = {
            "scenario": LABELS[s],
            "cross_subsidy_$m": net.clip(lower=0).sum() / 1e6,
            "within_25pct_%": ((r / cost - 1).abs() <= 0.25).mean() * 100,
            "suburb_cross_subsidy_$m": sub.clip(lower=0).sum() / 1e6,
            "tawa_net_$m": sub.get("Tawa", np.nan) / 1e6,
            "wellington_central_net_$m": sub.get("Wellington Central", np.nan) / 1e6,
        }
        for t in TYPES:
            m = f["ptype"] == t
            row[f"ratio_{t}"] = r[m].sum() / cost[m].sum()
        rows.append(row)
    return pd.DataFrame(rows).set_index("scenario")


def main():
    f = pd.read_parquet(PROC / "fiscal_units.parquet")
    bills, loading, uagc = scenarios(f)
    print(f"commercial loading today {loading:.2f}x per $ CV; max UAGC ${uagc:,.0f} per rating unit")
    res = {}
    for cost_col, name in [("cost_A", "per_unit"), ("cost_A_cv", "by_value")]:
        m = metrics(f, bills, cost_col)
        m.to_csv(TABLES / f"lv_alignment_{name}.csv", float_format="%.3f")
        res[name] = m
        print(f"\n== shared costs split {name}")
        print(m.round(2).to_string())
    med = bills.groupby(f["ptype"]).median().T[TYPES[:2]].rename(index=LABELS)
    med.round(0).to_csv(TABLES / "lv_alignment_median_bills.csv")
    print(med.round(0).to_string())
    figure(res)


def figure(res):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    names = list(LABELS.values())
    y = np.arange(len(names))
    ax = axes[0]
    for i, (k, lab) in enumerate([("per_unit", "Shared costs per property"), ("by_value", "Shared costs by value")]):
        v = res[k].loc[names, "cross_subsidy_$m"].values
        ax.barh(y + (-0.2 if i == 0 else 0.2), v, height=0.38, color=SERIES[i], label=lab)
        for yy, val in zip(y + (-0.2 if i == 0 else 0.2), v):
            ax.text(val + 2, yy, f"${val:.0f}m", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.set_xlabel("Cross-subsidy: rates paid above cost, $m a year (lower = closer)")
    ax.grid(axis="y", visible=False)
    ax.set_title("How much rates drift from cost", loc="left", fontsize=11, color=INK2)
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    ax.set_xlim(0, res["per_unit"]["cross_subsidy_$m"].max() * 1.2)

    ax = axes[1]
    m = res["per_unit"].loc[names]
    cols = [("ratio_Commercial", "Commercial", SERIES[1]), ("ratio_House", "House", SERIES[0]),
            ("ratio_Apartment / unit / flat", "Apartment", "#1baf7a")]
    for (c, lab, col) in cols:
        ax.plot(m[c].values, y, marker="o", ms=8, lw=0, color=col, label=lab)
    ax.axvline(1, color=INK, lw=1)
    ax.set_xscale("log")
    ax.set_xticks([0.5, 1, 2, 4], ["0.5×", "1×", "2×", "4×"])
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.grid(axis="x", which="minor", visible=False)
    ax.set_yticks(y, [""] * len(y))
    ax.invert_yaxis()
    ax.set_xlabel("Rates paid ÷ cost to serve (shared costs per property)")
    ax.set_title("Rates ÷ cost by property type", loc="left", fontsize=11, color=INK2)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3, frameon=False, fontsize=9)
    fig.suptitle("Would land value rating line Wellington's rates up with what properties cost?",
                 x=0.02, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(FIGS / "lv_alignment.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
