"""Build the interactive Tax Reset page (outputs/web/tax_reset_explorer.html).

Embeds simplified SA2 results (model_national.py), the decile and council tables, the
fiscal ledger and headline totals into web/tax_reset_template.html, with Leaflet's CSS
inlined (the artifact host only allows scripts from CDNs, not stylesheets).
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "web" / "tax_reset_template.html"
OUT = ROOT / "outputs" / "web" / "tax_reset_explorer.html"
LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"


def clean(v):
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), 2)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def main():
    res = pd.read_csv(ROOT / "outputs" / "tables" / "sa2_results.csv", index_col=0)
    g = gpd.read_parquet(ROOT / "data" / "processed" / "sa2_model.parquet")
    g = g[g["ta_name"] != "Chatham Islands Territory"]
    g = g.join(res[["pop", "owner_share", "eligible_earners", "n_super", "n_benefit", "region"]])
    g["geometry"] = g.geometry.simplify(60)
    g = g.to_crs(4326)
    feats = []
    for code, r in g.iterrows():
        props = {
            "code": int(code), "name": r["name"], "ta": r["ta_name"], "region": r["region"],
            "hh": r["households"], "pop": r["pop"], "own": r["owner_share"],
            "net": r["net_change_per_hh"], "inc": r["income_side_gain_per_hh"],
            "lvt_home": r["lvt_owner_occupiers_per_hh"], "lvt_farm": r["lvt_farm_local_per_hh"],
            "lv_unit": r["lv_res_per_unit"], "imputed": bool(r["lv_res_per_unit_imputed"]),
            "med_inc": r["median_household_income"], "earners": r["eligible_earners"],
            "super": r["n_super"], "benefit": r["n_benefit"],
        }
        geom = json.loads(gpd.GeoSeries([r.geometry]).to_json())["features"][0]["geometry"]

        def rnd(c):
            return [rnd(x) for x in c] if isinstance(c[0], list) else [round(c[0], 4), round(c[1], 4)]

        geom["coordinates"] = rnd(geom["coordinates"])
        feats.append({"type": "Feature", "properties": {k: clean(v) for k, v in props.items()}, "geometry": geom})
    geo = {"type": "FeatureCollection", "features": feats}

    dec = pd.read_csv(ROOT / "outputs" / "tables" / "net_change_by_area_income_decile.csv")
    ta = pd.read_csv(ROOT / "outputs" / "tables" / "net_change_by_ta.csv")
    ta = ta[~ta["ta_name"].str.contains("Area Outside")]
    ledger = pd.read_csv(ROOT / "outputs" / "tables" / "fiscal_ledger.csv")
    led = dict(zip(ledger["item"].str.strip(), ledger["nzd_bn"]))
    households = res["households"].sum()
    gap = -led["Fiscal balance (LVT + admin savings - income-side cost)"]
    unattributed = led["paid by landlords, businesses, others (not attributed)"]
    totals = dict(
        households=float(households),
        lvt_bn=led["LVT revenue (1.75% urban / 0.5% rural, after exemptions)"],
        lvt_claim_bn=led["TOP's claimed LVT revenue"],
        land_bn=led["Land value, all TAs (harvested + imputed)"],
        statsnz_land_bn=led["Stats NZ non-produced assets, Mar 2024 (check)"],
        ci_bn=led["Citizen's Income to eligible working-age adults"],
        tax_bn=led["Income tax change (TOP minus current)"],
        income_cost_bn=led["Net income-side cost"],
        lvt_owner_bn=led["paid by owner-occupiers (SA2 model)"],
        lvt_farm_bn=led["paid by farm land (SA2 model)"],
        lvt_other_bn=unattributed,
        admin_bn=led["TOP's claimed administrative savings"],
        gap_bn=gap,
        unfunded_per_hh=(gap + unattributed) * 1e9 / households,
        median_net=float(res["net_change_per_hh"].median()),
    )
    decs = [{k: clean(v) for k, v in r.items()} for r in dec.to_dict("records")]
    tas = [{k: clean(v) for k, v in r.items()} for r in ta.to_dict("records")]

    css = requests.get(LEAFLET_CSS, timeout=60).text
    html = TEMPLATE.read_text()
    html = html.replace("/*__LEAFLET_CSS__*/", css)
    html = html.replace("__DATA__", json.dumps(geo, separators=(",", ":")))
    html = html.replace("__DECILES__", json.dumps(decs, separators=(",", ":")))
    html = html.replace("__TAS__", json.dumps(tas, separators=(",", ":")))
    html = html.replace("__TOTALS__", json.dumps(totals, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB); unfunded per household ${totals['unfunded_per_hh']:,.0f}")


if __name__ == "__main__":
    main()
