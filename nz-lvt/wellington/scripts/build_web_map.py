"""Build the interactive "Who Pays Wellington" page (outputs/web/who_pays_wellington.html).

Embeds the SA1 fiscal GeoJSON (fiscal_blocks.py), the suburb net table and headline totals
into web/fiscal_map_template.html, with Leaflet's CSS inlined (the artifact host only allows
scripts from CDNs, not stylesheets).
"""

import json
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "web" / "fiscal_map_template.html"
OUT = ROOT / "outputs" / "web" / "who_pays_wellington.html"
LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"


def main():
    geo = json.loads((ROOT / "outputs" / "blocks" / "sa1_fiscal.geojson").read_text())
    for f in geo["features"]:
        f["properties"] = {k: (None if isinstance(v, float) and v != v else v) for k, v in f["properties"].items()}
    sub = pd.read_csv(ROOT / "outputs" / "tables" / "fiscal_net_by_suburb.csv", index_col=0)
    suburbs = [dict(name=n, units=int(r["units"]), rates=r["rates_$m"], cost=r["cost_$m"], net=r["net_$m"],
                    ratio=r["ratio"], commercial=r["commercial_rates_$m"], net_v1=r["net_v1_$m"],
                    lo=r["net_lo_$m"], hi=r["net_hi_$m"]) for n, r in sub.iterrows()]
    tables = ROOT / "outputs" / "tables"
    t = pd.read_csv(tables / "fiscal_v2_by_property_type.csv", index_col=0)
    loc = pd.read_csv(tables / "fiscal_v2_local_cost_by_rule.csv", index_col=[0, 1]).loc["homes_by_density"]
    u = pd.read_parquet(ROOT / "data" / "processed" / "fiscal_units_v2.parquet",
                        columns=["rates", "cost_A_v2", "cost_B_v2"])

    def ratios(ptype):
        r = t.loc[ptype]
        return dict(v2=float(r["ratio_A_v2"]), v1=float(r["ratio_A_v1"]), lo=float(r["v2_range_low"]),
                    hi=float(r["v2_range_high"]))

    totals = dict(
        units=len(u), rates_m=u["rates"].sum() / 1e6, cost_m=u["cost_A_v2"].sum() / 1e6,
        cost_full_m=u["cost_B_v2"].sum() / 1e6, gap_m=(u["cost_B_v2"].sum() - u["rates"].sum()) / 1e6,
        commercial=ratios("Commercial"), house=ratios("House"), apt=ratios("Apartment / unit / flat"),
        vacant=ratios("Residential vacant land"),
        local_low=float(loc["SA1 pool by lot width (v2)"].iloc[0]),
        local_high=float(loc["SA1 pool by lot width (v2)"].iloc[-1]),
    )
    css = requests.get(LEAFLET_CSS, timeout=60).text
    html = TEMPLATE.read_text()
    html = html.replace("/*__LEAFLET_CSS__*/", css)
    html = html.replace("__DATA__", json.dumps(geo, separators=(",", ":")))
    html = html.replace("__SUBURBS__", json.dumps(suburbs, separators=(",", ":")))
    html = html.replace("__TOTALS__", json.dumps(totals, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
