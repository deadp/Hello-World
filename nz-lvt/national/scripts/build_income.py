"""SA2-level income side of the Tax Reset model (Citizen's Income + income tax change).

For each SA2, the 2023 Census gives personal income for everyone 15+ in seven bands and
counts of people by income source. We split the 15+ population into:
  - 15-17 year olds (3/5 of the 15-19 age group): not eligible, assumed in the lowest band.
  - Superannuitants (NZ Super / Veteran's Pension as a source): held harmless by TOP, so
    no change. Removed from the $10k-$70k bands, starting with $20-30k and $30-50k.
  - People on a benefit the Citizen's Income replaces (Jobseeker, Sole Parent, Supported
    Living, Student Allowance): held harmless, no change. Removed from the lowest bands up.
  - Everyone else ("eligible earners"): gains the Citizen's Income minus the change in
    income tax, evaluated at an assumed mean income for their band.

This is an area-level approximation, not a household microsimulation: incomes are banded,
people on several benefits are counted once per source, and children's top-ups are not
modelled (TOP says parents gain, but the child schedule was not published in full).

Writes data/processed/sa2_income.csv.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from policy import CITIZENS_INCOME, current_tax, top_tax, working_age_gain

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "sa2_income.csv"

BANDS = ["le10k", "10_20k", "20_30k", "30_50k", "50_70k", "70_100k", "gt100k"]
# Assumed mean income within each band. The open top band uses $165k, which puts total
# census personal income near $222bn (sensitivity: $150k-$190k).
BAND_MEAN = {"le10k": 5_000, "10_20k": 15_000, "20_30k": 25_000, "30_50k": 40_000,
             "50_70k": 60_000, "70_100k": 85_000, "gt100k": 165_000}
SUPER_ORDER = ["20_30k", "30_50k", "10_20k", "50_70k"]
BENEFIT_ORDER = ["le10k", "10_20k", "20_30k", "30_50k"]


def load():
    code = "SA22023_V1_00"

    def table(name):
        df = pd.read_csv(RAW / f"census2023_sa2_{name}.csv")
        fields = pd.read_csv(RAW / f"census2023_sa2_{name}_fields.csv").set_index("field")["alias"]
        return df.set_index(code), fields

    ind1, f1 = table("individuals1")
    ind2, f2 = table("individuals2")
    hh, fh = table("households")

    def pick(df, fields, text):
        cols = fields[fields.str.contains(text, regex=False)].index
        if len(cols) != 1:
            raise KeyError(text)
        return df[cols[0]].where(lambda s: s >= 0)

    d = pd.DataFrame(index=ind1.index)
    d["name"] = ind1["SA22023_V1_00_NAME_ASCII"]
    d["pop"] = pick(ind1, f1, "Census usually resident population count (Total)")
    d["age15_19"] = pick(ind1, f1, "5-year groups - 15-19 years")
    young = ["0-4", "5-9", "10-14"]
    d["age0_14"] = sum(pick(ind1, f1, f"5-year groups - {a} years").fillna(0) for a in young)
    old = ["65-69", "70-74", "75-79", "80-84", "85-89"]
    d["age65p"] = sum(pick(ind1, f1, f"5-year groups - {a} years").fillna(0) for a in old) + \
        pick(ind1, f1, "90 years and over").fillna(0)
    labels = ["$10,000 or less", "$10,001-$20,000", "$20,001-$30,000", "$30,001-$50,000",
              "$50,001-$70,000", "$70,001-$100,000", "$100,001 or more"]
    for b, lab in zip(BANDS, labels):
        d[f"inc_{b}"] = pick(ind2, f2, f"Total personal income ({lab})").fillna(0)
    d["median_personal_income"] = pick(ind2, f2, "Total personal income (Median ($))")
    d["n_super"] = pick(ind2, f2, "(New Zealand Superannuation or Veteran's Pension)").fillna(0)
    d["n_benefit"] = sum(pick(ind2, f2, f"({b})").fillna(0) for b in
                         ["Jobseeker Support", "Sole Parent Support", "Supported Living Payment", "Student Allowance"])
    d["households"] = pick(hh, fh, "Total household income (Total)")
    d["median_household_income"] = pick(hh, fh, "Total household income (Median ($))")
    d["hh_owned"] = pick(hh, fh, "Tenure of household (Dwelling owned or partly owned)").fillna(0)
    d["hh_trust"] = pick(hh, fh, "Tenure of household (Dwelling held in a family trust)").fillna(0)
    d["hh_not_owned"] = pick(hh, fh, "Tenure of household (Dwelling not owned and not held").fillna(0)
    d["median_rent"] = pick(hh, fh, "Weekly rent paid by household (Median ($))")
    return d


def remove(counts, n, order):
    """Take n people out of the bands in `order`, proportionally within that set, then overflow."""
    counts = counts.copy()
    pool = counts[order].sum()
    take = min(n, pool)
    if pool > 0:
        counts[order] -= counts[order] / pool * take
    return counts


def split_population(d):
    rows = []
    for code, r in d.iterrows():
        bands = pd.Series({b: r[f"inc_{b}"] for b in BANDS}, dtype=float)
        u18 = 0.6 * (r["age15_19"] if pd.notna(r["age15_19"]) else 0)
        bands = remove(bands, u18, ["le10k", "10_20k"])
        bands = remove(bands, r["n_super"], SUPER_ORDER)
        bands = remove(bands, r["n_benefit"], BENEFIT_ORDER)
        rows.append(bands.rename(code))
    elig = pd.DataFrame(rows).clip(lower=0)
    elig.columns = [f"elig_{b}" for b in BANDS]
    return elig


def main():
    d = load()
    d = d[d["pop"].fillna(0) > 0]
    elig = split_population(d)
    d = d.join(elig)
    gains = {b: float(working_age_gain(BAND_MEAN[b])) for b in BANDS}
    tax_old = {b: float(current_tax(BAND_MEAN[b])) for b in BANDS}
    tax_new = {b: float(top_tax(BAND_MEAN[b])) for b in BANDS}
    d["eligible_earners"] = elig.sum(axis=1)
    d["citizens_income_paid"] = d["eligible_earners"] * CITIZENS_INCOME
    d["income_tax_change"] = sum(d[f"elig_{b}"] * (tax_new[b] - tax_old[b]) for b in BANDS)
    d["income_side_gain"] = sum(d[f"elig_{b}"] * gains[b] for b in BANDS)
    d["income_side_gain_per_household"] = d["income_side_gain"] / d["households"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(OUT)

    n = d[["pop", "households", "eligible_earners", "n_super", "n_benefit"]].sum()
    print(n.round(0).to_string())
    print(f"Citizen's Income to eligible earners: ${d['citizens_income_paid'].sum()/1e9:.1f}bn")
    print(f"Income tax change (TOP - current):    ${d['income_tax_change'].sum()/1e9:+.1f}bn")
    print(f"Net income-side transfer to households: ${d['income_side_gain'].sum()/1e9:.1f}bn")
    print(f"Median SA2 income-side gain per household: ${d['income_side_gain_per_household'].median():,.0f}")


if __name__ == "__main__":
    main()
