"""Fit a Wellington cycle-commuting uptake model to the 2018 and 2023 census.

The PCT's uptake models were fitted to English commuting. Here the same kind of model is fitted
to Wellington's own travel to work, SA2 of residence -> SA2 of workplace, 2018 and 2023 (the
2023 census table also carries 2018 counts on 2023 SA2 boundaries):

    logit(p) = b0 + b1 d + b2 sqrt(d) + b3 d^2 + b4 g + b5 sqrt(d) g
               + b6 busy + b7 protected + b8 y2023 + b9 y2023 x protected

  p          share of commuters (excluding work at home) who cycled
  d, g       route length (km) and average gradient (%) of the calibrated route (cycle_model.py),
             averaged over the SA1-to-workplace routes within each SA2 pair
  busy       share of the route on level 3-4 streets; protected: share on protected facilities
             (today's network, for both years)
  y2023      census year dummy; the interaction asks whether pairs whose routes are now protected
             gained more cycling between 2018 and 2023 (much of today's protected network was built
             in between, e.g. Hutt Road and Evans Bay; some after March 2023)

Grouped binomial GLM (statsmodels) on pairs with >= 20 commuters in the year, standard errors
clustered by origin SA2. Suppressed bicycle cells (-999, 1-5 people) count as 2.

Scenarios written for cycle_model.py:
  wellington_now     the fitted model on today's routes (checks fit)
  wellington_network every route fully low-stress: busy -> 0, protected share += former busy share
                     (Wellingtonians' current propensity, with a complete network)

Writes outputs/tables/cycle_uptake_local.json (coefficients, fit, scenario uplift) and
outputs/tables/cycle_uptake_local_fit.csv (observed vs fitted by distance and gradient band).
"""

import json

import numpy as np
import pandas as pd
import statsmodels.api as sm

import cycle_model as M

TERMS = ["d", "sqrt_d", "d2", "g", "sqrt_d_g", "busy", "prot", "y2023", "y2023_prot"]


def design(df):
    X = pd.DataFrame({"d": df["km"], "sqrt_d": np.sqrt(df["km"]), "d2": df["km"] ** 2, "g": df["grad"],
                      "sqrt_d_g": np.sqrt(df["km"]) * df["grad"], "busy": df["hs"], "prot": df["prot"],
                      "y2023": df["y2023"], "y2023_prot": df["y2023"] * df["prot"]})
    return sm.add_constant(X, has_constant="add")


def predict(coef, km, grad, busy, prot, y2023=1.0):
    km = np.asarray(km, dtype=float)
    x = (coef["const"] + coef["d"] * km + coef["sqrt_d"] * np.sqrt(km) + coef["d2"] * km ** 2 + coef["g"] * grad
         + coef["sqrt_d_g"] * np.sqrt(km) * grad + coef["busy"] * busy + coef["prot"] * prot
         + coef["y2023"] * y2023 + coef["y2023_prot"] * y2023 * prot)
    return 1 / (1 + np.exp(-x))


def main():
    params = M.load_params()
    *_, metrics = M.run(params, scenarios=(), verbose=False, collect_od=True, only=["work"])
    raw = pd.read_csv(M.CYC / "census_work_od.csv", encoding="utf-8-sig",
                      dtype={"SA22023_V1_00_usual_residence_address": str, "SA22023_V1_00_workplace_address": str})
    raw = raw.rename(columns={"SA22023_V1_00_usual_residence_address": "o", "SA22023_V1_00_workplace_address": "d"})
    rows = []
    for yr in ("2018", "2023"):
        t = raw[["o", "d"]].copy()
        tot = raw[f"{yr}_Total_stated"]
        home = raw[f"{yr}_Work_at_home"].clip(lower=0)
        t["n"] = (tot - home).where(tot >= 0)
        t["y"] = raw[f"{yr}_Bicycle"].where(raw[f"{yr}_Bicycle"] >= 0, 2.0)
        t["y2023"] = 1.0 if yr == "2023" else 0.0
        rows.append(t)
    df = pd.concat(rows).merge(metrics[metrics["stream"] == "work"], on=["o", "d"])
    df = df[(df["n"] >= 20) & (df["km"] > 0)].copy()
    df["y"] = df[["y", "n"]].min(axis=1)
    print(f"pairs x years: {len(df):,} ({df['n'].sum():,.0f} commuters, {df['y'].sum():,.0f} cycled)")
    X = design(df)
    endog = np.column_stack([df["y"], df["n"] - df["y"]])
    fit = sm.GLM(endog, X, family=sm.families.Binomial()).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(df["o"])[0]})
    print(fit.summary2().tables[1].round(4).to_string())
    coef = fit.params.to_dict()
    df["fitted"] = fit.predict(X) * df["n"]
    dev_null = sm.GLM(endog, np.ones((len(df), 1)), family=sm.families.Binomial()).fit().deviance
    pseudo_r2 = 1 - fit.deviance / dev_null
    obs_share = df["y"].sum() / df["n"].sum()
    corr = np.corrcoef(df["y"] / df["n"], df["fitted"] / df["n"])[0, 1]
    print(f"deviance explained {pseudo_r2:.3f}; correlation of shares {corr:.3f}; observed share {obs_share:.4f}")

    # Fit by band (2023).
    d23 = df[df["y2023"] == 1].copy()
    d23["km_band"] = pd.cut(d23["km"], [0, 2, 4, 6, 8, 12, 20, 60])
    d23["grad_band"] = pd.cut(d23["grad"], [0, 1.5, 2.5, 3.5, 5, 20])
    bands = []
    for col in ("km_band", "grad_band"):
        g = d23.groupby(col, observed=True).agg(n=("n", "sum"), observed=("y", "sum"), fitted=("fitted", "sum"))
        g["observed_%"] = g["observed"] / g["n"] * 100
        g["fitted_%"] = g["fitted"] / g["n"] * 100
        g.index = [f"{col}: {i}" for i in g.index]
        bands.append(g)
    bands = pd.concat(bands)
    bands.round(2).to_csv(M.TABLES / "cycle_uptake_local_fit.csv")
    print(bands.round(2).to_string())

    # Scenario: complete low-stress network, with Wellington's current propensity.
    base = predict(coef, d23["km"], d23["grad"], d23["hs"], d23["prot"])
    full = predict(coef, d23["km"], d23["grad"], 0.0, (d23["prot"] + d23["hs"]).clip(upper=1))
    pgd = M.uptake(d23["km"].values, d23["grad"].values)
    summary = dict(
        n_obs=int(len(df)), commuters=float(df["n"].sum()), cyclists=float(df["y"].sum()),
        deviance_explained=float(pseudo_r2), share_correlation=float(corr),
        coef=coef, se=fit.bse.to_dict(), p=fit.pvalues.to_dict(),
        shares_2023_pct=dict(observed=float(d23["y"].sum() / d23["n"].sum() * 100),
                             fitted_today=float((base * d23["n"]).sum() / d23["n"].sum() * 100),
                             full_low_stress_network=float((full * d23["n"]).sum() / d23["n"].sum() * 100),
                             pct_go_dutch_2020=float((pgd * d23["n"]).sum() / d23["n"].sum() * 100)),
        params=params)
    (M.TABLES / "cycle_uptake_local.json").write_text(json.dumps(summary, indent=1, default=float))
    print(json.dumps(summary["shares_2023_pct"], indent=1))


if __name__ == "__main__":
    main()
