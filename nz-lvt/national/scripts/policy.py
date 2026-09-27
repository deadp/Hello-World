"""Tax and transfer parameters: current NZ settings vs The Opportunity Party's 2026 "Tax Reset".

Sources (checked Sept 2026):
  - TOP policy page (opportunity.org.nz/tax-reset): 1.75% urban LVT, 0.5% rural LVT,
    Citizen's Income $19,400/yr tax-free for residents 18+, three brackets 28/34/39%,
    superannuitants and current beneficiaries held harmless, retiree LVT deferral.
  - Bracket thresholds ($50k / $200k) are from secondary sources (Deloitte Tax Alert
    13 Jul 2026, Johnston Law 11 Aug 2026); TOP's full policy PDF was not retrievable.
  - Current schedule: IRD rates from 31 July 2024, Independent Earner Tax Credit (IETC).
"""

import numpy as np

# Current personal income tax (from 31 July 2024).
CURRENT_BRACKETS = [(15_600, 0.105), (53_500, 0.175), (78_100, 0.30), (180_000, 0.33), (np.inf, 0.39)]
IETC_AMOUNT = 520
IETC_MIN, IETC_FULL_TO, IETC_ABATE = 24_000, 66_000, 0.13

# TOP 2026 Tax Reset.
TOP_BRACKETS = [(50_000, 0.28), (200_000, 0.34), (np.inf, 0.39)]
CITIZENS_INCOME = 19_400
LVT_URBAN = 0.0175
LVT_RURAL = 0.005
TOP_CLAIMED_LVT_REVENUE = 24.3e9  # Johnston Law summary of the policy document
TOP_CLAIMED_LAND_VALUE = 1.7e12


def bracket_tax(income, brackets):
    income = np.asarray(income, dtype=float)
    tax = np.zeros_like(income)
    lower = 0.0
    for upper, rate in brackets:
        tax += rate * np.clip(income - lower, 0, upper - lower)
        lower = upper
    return tax


def ietc(income):
    income = np.asarray(income, dtype=float)
    full = np.where((income >= IETC_MIN) & (income <= IETC_FULL_TO), IETC_AMOUNT, 0.0)
    abated = np.clip(IETC_AMOUNT - IETC_ABATE * (income - IETC_FULL_TO), 0, IETC_AMOUNT)
    return np.where(income > IETC_FULL_TO, abated, full)


def current_tax(income):
    """Income tax net of IETC for a working-age earner not on a benefit or Working for Families."""
    return bracket_tax(income, CURRENT_BRACKETS) - ietc(income)


def top_tax(income):
    return bracket_tax(income, TOP_BRACKETS)


def working_age_gain(income):
    """Annual cash change for an eligible working-age adult (not on a replaced benefit or NZ Super)."""
    return CITIZENS_INCOME - (top_tax(income) - current_tax(income))


if __name__ == "__main__":
    for y in [0, 10_000, 30_000, 50_000, 70_000, 100_000, 150_000, 200_000, 300_000]:
        print(f"${y:>9,}: current tax {current_tax(y):>9,.0f}  TOP tax {top_tax(y):>9,.0f}  "
              f"net gain {working_age_gain(y):>9,.0f}")
