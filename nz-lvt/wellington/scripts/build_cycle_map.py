"""Build the interactive cycle gap map (outputs/web/wellington_cycle_gaps.html).

Embeds outputs/blocks/cycle_edges.geojson (cycle_gaps.py), the top corridors, the prioritised
candidates and build order (cycle_priorities.py) and headline figures into web/cycle_map_template.html, with Leaflet's CSS inlined.
"""

import json
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "outputs" / "tables"
TEMPLATE = ROOT / "web" / "cycle_map_template.html"
OUT = ROOT / "outputs" / "web" / "wellington_cycle_gaps.html"
LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"
N_LIST = 30


def main():
    geo = json.loads((ROOT / "outputs" / "blocks" / "cycle_edges.geojson").read_text())
    for f in geo["features"]:
        p = f["properties"]
        f["properties"] = {k: (None if isinstance(v, float) and v != v else v) for k, v in p.items()
                           if k not in ("gap",)}
    c = pd.read_csv(TABLES / "cycle_corridors.csv").head(N_LIST)
    c["suburbs"] = c["suburbs"].fillna("–")
    corr = json.loads(c[["rank", "street", "road", "suburbs", "length_m", "census_trips", "godutch_trips",
                         "ebike_trips", "max_adt", "speed", "painted_share", "facility_now", "one_way_share",
                         "plan", "rank_local", "rank_census"]].to_json(orient="records"))
    sc_all = pd.read_csv(TABLES / "cycle_scenarios.csv", index_col=0)
    sc = sc_all.loc["all"]
    commute = sc_all.loc[[i for i in ("work", "primary", "secondary", "tertiary") if i in sc_all.index]].sum()
    sm = pd.read_csv(TABLES / "cycle_summary.csv", index_col=0)
    top = c.head(20)["plan"]
    summary = dict(
        scen={k: dict(share=sc[k] / sc["trips"] * 100, commute=commute[k] / commute["trips"] * 100)
              for k in ("census", "godutch", "ebike", "local")},
        trips=dict(all=float(sc["trips"]), commute=float(commute["trips"])),
        km=sm["cycle_km_per_day"].to_dict(), prot=sm["on_protected_%"].to_dict(),
        quiet=sm["on_quiet_streets_%"].to_dict(), stress=sm["on_high_stress_%"].to_dict(),
        top20=dict(planned=int(top.isin(["Planned (WCC)", "Unfunded (ex-LGWM)", "Desired only"]).sum()),
                   lgwm=int((top == "Unfunded (ex-LGWM)").sum()), notplan=int((top == "Not in plan").sum())),
    )
    loc = TABLES / "cycle_uptake_local.json"
    if loc.exists():
        summary["local"] = json.loads(loc.read_text())["shares_2023_pct"]
    rob = c[["rank", "rank_local", "rank_census"]].head(20)
    summary["robust"] = dict(local=int((rob["rank_local"] <= 20).sum()), census=int((rob["rank_census"] <= 20).sum()))
    cal = TABLES / "cycle_calibration.json"
    if cal.exists():
        cj = json.loads(cal.read_text())
        summary["calibration"] = dict(best=cj["best"], default=cj["default"])
    pts = json.loads((ROOT / "outputs" / "blocks" / "cycle_points.geojson").read_text())
    for f in pts["features"]:
        f["properties"] = {k: (None if isinstance(v, float) and v != v else v) for k, v in f["properties"].items()}
    pri, build = None, None
    if (ROOT / "outputs" / "blocks" / "cycle_priorities.geojson").exists():
        pri = json.loads((ROOT / "outputs" / "blocks" / "cycle_priorities.geojson").read_text())
        for f in pri["features"]:
            f["properties"] = {k: (None if isinstance(v, float) and v != v else v) for k, v in f["properties"].items()}
        b = pd.read_csv(TABLES / "cycle_build_order.csv")
        build = json.loads(b.to_json(orient="records"))
        conn = pd.read_csv(TABLES / "cycle_connectivity.csv")
        conn = conn[(conn["tolerance_m"] == 0) & (conn["cap"] == 1.25)].set_index("scenario")["connected_pct"]
        summary["conn"] = dict(now=float(conn["godutch"]), census=float(conn["census"]), local=float(conn["local"]),
                               ebike=float(conn["ebike"]))
    html = TEMPLATE.read_text()
    html = html.replace("/*__LEAFLET_CSS__*/", requests.get(LEAFLET_CSS, timeout=60).text)
    html = html.replace("__DATA__", json.dumps(geo, separators=(",", ":")))
    html = html.replace("__CORRIDORS__", json.dumps(corr, separators=(",", ":")))
    html = html.replace("__SUMMARY__", json.dumps(summary, separators=(",", ":")))
    html = html.replace("__POINTS__", json.dumps(pts, separators=(",", ":")))
    html = html.replace("__PRIORITIES__", json.dumps(pri, separators=(",", ":")))
    html = html.replace("__BUILD__", json.dumps(build, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB); top 20 plan: {top.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
