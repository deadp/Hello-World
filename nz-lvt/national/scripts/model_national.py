"""National Tax Reset model: land value tax + Citizen's Income + income tax change, by SA2.

Inputs: data/processed/sa2_income.csv (build_income.py) and sa2_land.csv / ta_land.csv /
land_coverage.csv (build_land.py).

Land value for places without a public council layer
  - SA2 residential land value per rating unit is imputed from a regression fitted on
    covered SA2s: log(res LV per unit) ~ log(median household income) + log(median rent)
    + home-ownership share + urban/rural class + region.
  - TA totals for uncovered TAs = LINZ rating units x the land value per unit of covered
    TAs in the same region (unit-weighted).

Who pays the land value tax in this model
  - Owner-occupiers (census "owned" + "family trust" households): their SA2's average
    residential land value per rating unit x 1.75% (0.5% for lifestyle blocks).
    Superannuitants can defer; the liability is counted when it accrues.
  - Farm land (rural, non-residential) at 0.5%: charged to households in the SA2 where
    the land is, on the view that most farms are owner-operated.
  - Rented housing, commercial, industrial and other urban land: paid by landlords and
    business owners whose place of residence is unknown, so it appears only in the
    national fiscal ledger, not in SA2 household results.
  - DOC public conservation land is exempt (identified spatially in build_land.py).
    Other exempt land (government, clubs, religious, communal Maori land, social housing)
    is approximated by Stats NZ's share of land held by central/local government and
    non-profits (7.3%) less the conservation land already removed, applied to urban land.
Renters bear no land tax (Doucet and the incidence literature: a land tax is not passed
on to tenants absent rent control).

Outputs: outputs/tables/*.csv, outputs/figures/*.png, data/processed/sa2_model.parquet
"""

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from policy import LVT_RURAL, LVT_URBAN, TOP_CLAIMED_LVT_REVENUE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
TABLES = ROOT / "outputs" / "tables"
FIGS = ROOT / "outputs" / "figures"

# Land held by central/local government and non-profits (Stats NZ annual balance sheets,
# March 2024), a proxy for exempt land. DOC conservation land identified in the council
# data is removed explicitly, and its value is netted off this share (see main()).
GOV_NPISH_SHARE = (47.8 + 45.7 + 26.2) / 1634.6
STATSNZ_LAND_TOTAL = 1634.6e9
TOP_ADMIN_SAVINGS = 1.7e9
MIN_UNITS = 30  # SA2s with fewer residential rating units are imputed
MIN_MAP_HOUSEHOLDS = 50

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.axisbelow": True,
})


def title(ax, text, sub):
    kw = dict(xy=(0, 1), xycoords="axes fraction", textcoords="offset points", ha="left", va="bottom")
    ax.annotate(text, xytext=(0, 26), fontsize=13, color=INK, fontweight="bold", **kw)
    ax.annotate(sub, xytext=(0, 10), fontsize=9.5, color=INK2, **kw)


def sa2_geography():
    sa2 = gpd.read_file(RAW / "sa2_2023_clipped.geojson").to_crs(2193)
    sa2["code"] = sa2["SA22023_V1_00"].astype(int)
    pts = sa2[["code", "geometry"]].copy()
    pts["geometry"] = sa2.geometry.representative_point()
    ta = gpd.read_file(RAW / "ta_2025.geojson").to_crs(2193)
    ur = gpd.read_file(RAW / "urban_rural_2023.geojson").to_crs(2193)
    j = gpd.sjoin(pts, ta[["ta_name", "rc_name", "geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    k = gpd.sjoin(pts, ur[["IUR2023_V1_00_NAME", "geometry"]], how="left", predicate="within")
    k = k[~k.index.duplicated()]
    geo = pd.DataFrame({"code": sa2["code"], "ta_name": j["ta_name"], "region": j["rc_name"],
                        "iur": k["IUR2023_V1_00_NAME"]}).set_index("code")
    return sa2.set_index("code")[["geometry"]], geo


def impute_residential(d):
    """Fill residential land value per rating unit where the SA2 has too few observed units."""
    x = pd.DataFrame(index=d.index)
    x["inc"] = np.log(d["median_household_income"])
    x["rent"] = np.log(d["median_rent"])
    x["own"] = (d["hh_owned"] + d["hh_trust"]) / d["households"]
    dummies = pd.get_dummies(d["iur"].fillna("NA"), prefix="ur", drop_first=True).astype(float)
    region = pd.get_dummies(d["region"].fillna("NA"), prefix="rc", drop_first=True).astype(float)
    x = pd.concat([x, dummies, region], axis=1)
    x.insert(0, "const", 1.0)
    y = np.log(d["lv_res_per_unit"])
    train = (d["res_units"] >= MIN_UNITS) & y.notna() & x.notna().all(axis=1) & np.isfinite(y)
    beta, *_ = np.linalg.lstsq(x[train].values, y[train].values, rcond=None)
    pred = pd.Series(x.fillna(x[train].median()).values @ beta, index=d.index)
    resid = y[train] - pred[train]
    r2 = 1 - (resid ** 2).sum() / ((y[train] - y[train].mean()) ** 2).sum()
    smear = np.exp(resid).mean()  # Duan smearing for the log model
    need = ~train
    d["lv_res_per_unit_imputed"] = need
    d.loc[need, "lv_res_per_unit"] = np.exp(pred[need]) * smear
    # Lifestyle share of residential land value: use the regional average where imputed.
    share = (d["lv_res_rural"] / d["lv_res"]).where(train)
    d["rural_res_share"] = share.fillna(share.groupby(d["region"]).transform("mean")).fillna(0)
    print(f"residential LV imputation: R2 {r2:.2f} on {train.sum()} SA2s; imputed {need.sum()} SA2s")
    return d, r2


def ta_totals(geo_ta_land, coverage):
    """Total land value by TA; impute TAs without a public layer from their region."""
    cov = coverage.set_index("ta_name")
    cov["region"] = geo_ta_land
    covered = cov["unit_coverage_%"] >= 60
    per_unit = (cov.loc[covered, "lv_bn"] * 1e9).groupby(cov.loc[covered, "region"]).sum() / \
        cov.loc[covered, "linz_units"].groupby(cov.loc[covered, "region"]).sum()
    national = (cov.loc[covered, "lv_bn"].sum() * 1e9) / cov.loc[covered, "linz_units"].sum()
    rate = cov["region"].map(per_unit).fillna(national)
    cov["lv_model"] = np.where(covered, cov["lv_bn"] * 1e9, cov["linz_units"] * rate)
    cov["lv_imputed"] = ~covered
    return cov


def main():
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    inc = pd.read_csv(PROC / "sa2_income.csv", index_col=0)
    land = pd.read_csv(PROC / "sa2_land.csv", index_col=0)
    land.index = land.index.astype(int)
    shapes, geo = sa2_geography()
    d = inc.join(geo).join(land.drop(columns=["sources"]), how="left")
    d = d[d["households"].fillna(0) > 0]
    for c in ["res_units", "lv_res", "lv_res_rural", "lv_farm", "lv", "lv_rural", "lv_urban"]:
        d[c] = d[c].fillna(0)
    d, r2 = impute_residential(d)

    # Owner-occupier and local farm land value tax (annual liability).
    owners = d["hh_owned"] + d["hh_trust"]
    rate_res = LVT_URBAN * (1 - d["rural_res_share"]) + LVT_RURAL * d["rural_res_share"]
    d["lvt_owner_occupiers"] = owners * d["lv_res_per_unit"] * rate_res
    d["lvt_farm_local"] = d["lv_farm"] * LVT_RURAL
    d["net_change"] = d["income_side_gain"] - d["lvt_owner_occupiers"] - d["lvt_farm_local"]
    for c in ["income_side_gain", "lvt_owner_occupiers", "lvt_farm_local", "net_change"]:
        d[f"{c}_per_hh"] = d[c] / d["households"]
    d["owner_share"] = owners / d["households"]

    # National fiscal ledger.
    coverage = pd.read_csv(PROC / "land_coverage.csv")
    ta_land = pd.read_csv(PROC / "ta_land.csv", index_col=0)
    region_of_ta = geo.dropna(subset=["ta_name"]).groupby("ta_name")["region"].agg(lambda s: s.mode().iat[0])
    cov = ta_totals(region_of_ta, coverage)
    rural_share = (ta_land["lv_rural"] / ta_land["lv"]).reindex(cov.index)
    region_rural = rural_share.groupby(cov["region"]).mean()
    rural_share = rural_share.fillna(cov["region"].map(region_rural)).fillna(rural_share.mean())
    cov["lv_rural_model"] = cov["lv_model"] * rural_share
    cons = ta_land["lv_conservation"].reindex(cov.index).fillna(0)
    cons_share = cons.sum() / cov["lv_model"].sum()
    exempt_share = max(GOV_NPISH_SHARE - cons_share, 0)
    urban_share = (ta_land["lv_urban"] / ta_land["lv"]).reindex(cov.index)
    urban_share = urban_share.fillna(1 - rural_share)
    cov["lv_urban_model"] = cov["lv_model"] * urban_share
    cov["lv_conservation_model"] = cons
    cov["lvt"] = (cov["lv_urban_model"] * LVT_URBAN * (1 - exempt_share) + cov["lv_rural_model"] * LVT_RURAL)
    print(f"conservation land {cons_share:.1%} of land value; further exempt share on urban land {exempt_share:.1%}")
    cov.to_csv(TABLES / "ta_land_value_and_lvt.csv")

    lv_total = cov["lv_model"].sum()
    lvt_total = cov["lvt"].sum()
    attributed = d["lvt_owner_occupiers"].sum() + d["lvt_farm_local"].sum()
    ledger = pd.DataFrame([
        ("Land value, all TAs (harvested + imputed)", lv_total),
        ("  of which imputed (no public layer)", cov.loc[cov["lv_imputed"], "lv_model"].sum()),
        ("  Stats NZ non-produced assets, Mar 2024 (check)", STATSNZ_LAND_TOTAL),
        ("  of which DOC conservation land (exempt)", cov["lv_conservation_model"].sum()),
        ("LVT revenue (1.75% urban / 0.5% rural, after exemptions)", lvt_total),
        ("  TOP's claimed LVT revenue", TOP_CLAIMED_LVT_REVENUE),
        ("  paid by owner-occupiers (SA2 model)", d["lvt_owner_occupiers"].sum()),
        ("  paid by farm land (SA2 model)", d["lvt_farm_local"].sum()),
        ("  paid by landlords, businesses, others (not attributed)", lvt_total - attributed),
        ("Citizen's Income to eligible working-age adults", d["citizens_income_paid"].sum()),
        ("Income tax change (TOP minus current)", d["income_tax_change"].sum()),
        ("Net income-side cost", d["income_side_gain"].sum()),
        ("TOP's claimed administrative savings", TOP_ADMIN_SAVINGS),
        ("Fiscal balance (LVT + admin savings - income-side cost)",
         lvt_total + TOP_ADMIN_SAVINGS - d["income_side_gain"].sum()),
    ], columns=["item", "nzd"])
    ledger["nzd_bn"] = (ledger["nzd"] / 1e9).round(1)
    ledger[["item", "nzd_bn"]].to_csv(TABLES / "fiscal_ledger.csv", index=False)
    print(ledger[["item", "nzd_bn"]].to_string(index=False))

    # Distribution across SA2s by median household income (household-weighted deciles).
    s = d.dropna(subset=["median_household_income"]).sort_values("median_household_income")
    cum = s["households"].cumsum() / s["households"].sum()
    s["income_decile"] = np.minimum((cum * 10).apply(np.ceil).astype(int), 10)
    g = s.groupby("income_decile")
    dec = pd.DataFrame({
        "households": g["households"].sum(),
        "median_household_income": g.apply(lambda x: np.average(x["median_household_income"], weights=x["households"])),
        "owner_share": g.apply(lambda x: np.average(x["owner_share"], weights=x["households"])),
        "income_side_gain_per_hh": g["income_side_gain"].sum() / g["households"].sum(),
        "lvt_owner_per_hh": g["lvt_owner_occupiers"].sum() / g["households"].sum(),
        "lvt_farm_per_hh": g["lvt_farm_local"].sum() / g["households"].sum(),
        "net_change_per_hh": g["net_change"].sum() / g["households"].sum(),
    })
    dec["net_change_%_of_income"] = dec["net_change_per_hh"] / dec["median_household_income"] * 100
    dec.to_csv(TABLES / "net_change_by_area_income_decile.csv")
    print(dec.round(0).to_string())

    by_ta = d.groupby("ta_name").apply(lambda x: pd.Series({
        "households": x["households"].sum(),
        "income_side_gain_per_hh": x["income_side_gain"].sum() / x["households"].sum(),
        "lvt_owner_per_hh": x["lvt_owner_occupiers"].sum() / x["households"].sum(),
        "lvt_farm_per_hh": x["lvt_farm_local"].sum() / x["households"].sum(),
        "net_change_per_hh": x["net_change"].sum() / x["households"].sum(),
        "share_sa2_imputed_%": np.average(x["lv_res_per_unit_imputed"], weights=x["households"]) * 100,
    }))
    by_ta.sort_values("net_change_per_hh").to_csv(TABLES / "net_change_by_ta.csv")

    out = d.copy()
    out.to_csv(TABLES / "sa2_results.csv")
    gdf = shapes.join(d[["name", "ta_name", "households", "net_change_per_hh", "income_side_gain_per_hh",
                         "lvt_owner_occupiers_per_hh", "lvt_farm_local_per_hh", "lv_res_per_unit",
                         "lv_res_per_unit_imputed", "median_household_income"]], how="inner")
    gdf.to_parquet(PROC / "sa2_model.parquet")
    figures(dec, gdf, ledger)


def figures(dec, gdf, ledger):
    fig, ax = plt.subplots(figsize=(9, 5))
    x = np.arange(1, 11)
    ax.bar(x - 0.2, dec["income_side_gain_per_hh"] / 1000, width=0.38, color=SERIES[0],
           label="Citizen's Income minus extra income tax")
    ax.bar(x + 0.2, -(dec["lvt_owner_per_hh"] + dec["lvt_farm_per_hh"]) / 1000, width=0.38, color=SERIES[1],
           label="Land value tax (owner-occupied homes + local farmland)")
    ax.plot(x, dec["net_change_per_hh"] / 1000, color=INK, marker="o", lw=2, label="Net change")
    for xi, v in zip(x, dec["net_change_per_hh"] / 1000):
        ax.annotate(f"{v:+.1f}k", (xi, v), textcoords="offset points", xytext=(0, 11), ha="center",
                    fontsize=8.5, color=INK2)
    ax.axhline(0, color=AXIS, lw=1)
    ax.set_xticks(x, [f"D{i}" for i in x])
    ax.set_xlabel("SA2s grouped into deciles of households by the area's median household income (D1 = lowest)")
    ax.set_ylabel("$ thousand per household per year")
    ax.grid(axis="x", visible=False)
    title(ax, "Tax Reset: average change per household, by area income",
          "2023 Census SA2s; LVT on rentals and business land not attributed to households")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=1, frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGS / "net_change_by_area_income_decile.png", dpi=160)
    plt.close(fig)

    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    div = LinearSegmentedColormap.from_list(
        "div", ["#a32322", "#e34948", "#f4a3a2", "#f0efec", "#9ec5f4", "#3987e5", "#184f95"])
    norm = TwoSlopeNorm(vcenter=0, vmin=-20000, vmax=40000)
    mainland = gdf[gdf["ta_name"] != "Chatham Islands Territory"]
    thin = mainland["households"] < MIN_MAP_HOUSEHOLDS
    fig = plt.figure(figsize=(13, 9.5))
    panels = [
        (fig.add_axes([0.00, 0.01, 0.46, 0.86]), (1_080_000, 4_760_000, 2_100_000, 6_200_000), "New Zealand"),
        (fig.add_axes([0.47, 0.49, 0.30, 0.40]), (1_735_000, 5_895_000, 1_790_000, 5_945_000), "Auckland"),
        (fig.add_axes([0.47, 0.03, 0.30, 0.42]), (1_560_000, 5_170_000, 1_590_000, 5_195_000), "Christchurch"),
    ]
    for ax, (xmin, ymin, xmax, ymax), label in panels:
        mainland[thin].plot(ax=ax, color=GRID, linewidth=0)
        mainland[~thin].plot(ax=ax, column="net_change_per_hh", cmap=div, norm=norm, linewidth=0.05,
                             edgecolor=SURFACE)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.set_title(label, loc="left", fontsize=11, color=INK2)
    cax = fig.add_axes([0.82, 0.25, 0.015, 0.5])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=div), cax=cax)
    cb.set_label("Net change per household, $/yr (blue gains, red loses)", color=INK2)
    cb.ax.yaxis.set_major_formatter(lambda v, _: f"{v / 1000:+.0f}k")
    cb.outline.set_visible(False)
    fig.text(0.01, 0.975, "Tax Reset: net change per household by SA2", fontsize=14, fontweight="bold",
             color=INK, va="top")
    fig.text(0.01, 0.945, f"Citizen's Income + income tax change − LVT on owner-occupied homes and local farmland. "
             f"Grey: SA2s with <{MIN_MAP_HOUSEHOLDS} households.", fontsize=9.5, color=INK2, va="top")
    fig.savefig(FIGS / "map_net_change_per_household.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
