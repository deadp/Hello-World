"""Parcel-level land value per m2 map for a region (default: Wellington Region).

The national harvest keeps one point per rating unit, which piles a whole farm's land
value onto a single spot. For a map we need the parcel shapes, so this script re-fetches
the region's council layers with (lightly simplified) polygons and spreads each rating
unit's land value across its parcels:
  - a unit covering several parcels (common for farms) is split by parcel area;
  - several units sharing one parcel polygon (flats, unit titles) are summed.
Land value per m2 = land value on the parcel / parcel area.

Upper Hutt publishes capital value only; its land value is taken from the national
build (imputed there from Hutt City land/capital value ratios).

Usage: python scripts/map_region.py
Writes outputs/figures/map_wellington_region_land_value.png and
outputs/regions/wellington_region_parcels.parquet (not committed; rebuildable).
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from harvest_land import SOURCES, WELLINGTON_RAW, get, num  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
FIGS = ROOT / "outputs" / "figures"
CACHE = ROOT / "data" / "processed" / "wellington_region_parcels.parquet"

REGION_SOURCES = ["hutt", "upper_hutt", "porirua", "kapiti", "wairarapa"]
SIMPLIFY_M = 2
PAGE = 1000

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#dcdad2"
BLUE_RAMP = ["#b7d3f6", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
BINS = [0, 20, 100, 400, 800, 1500, 3000, np.inf]
LABELS = ["<$20", "$20–100", "$100–400", "$400–800", "$800–1,500", "$1,500–3,000", "$3,000+"]


def fetch_polygons(name):
    url, vid, lv, cv, _cat, where = SOURCES[name]
    meta = get(url, {"f": "json"})
    oid = next(f["name"] for f in meta["fields"] if f["type"] == "esriFieldTypeOID")
    page = min(meta.get("maxRecordCount") or PAGE, 2000)
    count = get(f"{url}/query", {"where": where, "returnCountOnly": "true", "f": "json"})["count"]
    fields = [f for f in (vid, lv, cv) if f]

    def one(offset):
        d = get(f"{url}/query", {
            "where": where, "outFields": ",".join(fields), "orderByFields": oid, "resultOffset": offset,
            "resultRecordCount": page, "returnGeometry": "true", "outSR": 2193, "geometryPrecision": 1,
            "maxAllowableOffset": SIMPLIFY_M, "f": "geojson"})
        return d["features"]

    rows = []
    with ThreadPoolExecutor(6) as ex:
        for feats in ex.map(one, range(0, count, page)):
            for f in feats:
                if not f.get("geometry"):
                    continue
                p = f["properties"]
                rows.append({"vid": str(p.get(vid)).strip(), "lv": num(p.get(lv)) if lv else np.nan,
                             "geometry": shape(f["geometry"])})
    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=2193)
    g["source"] = name
    print(f"{name}: {len(g):,} parcel rows")
    return g


def wellington_city():
    p = gpd.read_file(WELLINGTON_RAW).to_crs(2193)
    parcel = p["RollNumber"] + "-" + p["AssessmentNumber"]
    has_children = set(parcel[p["AssessmentSuffix"].notna()])
    p = p[~(p["AssessmentSuffix"].isna() & parcel.isin(has_children))]
    return gpd.GeoDataFrame({"vid": p["ValuationID"], "lv": p["LandValue"], "source": "wellington_city"},
                            geometry=p.geometry, crs=2193)


def spread(g):
    """Spread unit land value over its parcels, then sum units sharing a parcel polygon."""
    g = g[g.geometry.notna() & ~g.geometry.is_empty].copy()
    g["wkb"] = g.geometry.to_wkb()
    g = g.drop_duplicates(["vid", "wkb"])
    g["area"] = g.geometry.area
    unit_area = g.groupby("vid")["area"].transform("sum")
    # Unit-title rows repeat the parent polygon for every unit: for those, the split below
    # divides by the number of distinct polygons (1), so each unit keeps its full value.
    g["lv_part"] = g["lv"] * g["area"] / unit_area
    agg = g.groupby("wkb").agg(lv=("lv_part", "sum"), area=("area", "first"), source=("source", "first"),
                               units=("vid", "nunique"))
    out = gpd.GeoDataFrame(agg.reset_index(drop=True),
                           geometry=gpd.GeoSeries.from_wkb(agg.index.values, crs=2193))
    out = out[(out["lv"] > 0) & (out["area"] > 10)]
    out["lv_m2"] = out["lv"] / out["area"]
    return out


def build():
    frames = [fetch_polygons(n) for n in REGION_SOURCES] + [wellington_city()]
    g = pd.concat(frames, ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs=2193)
    # Upper Hutt: use the land value imputed in the national build.
    units = pd.read_parquet(PROC / "land_units.parquet", columns=["source", "vid", "lv"])
    def norm(v):
        return v.astype(str).str.replace(r"\.0$", "", regex=True)

    uh = units[units["source"] == "upper_hutt"]
    uh = pd.Series(uh["lv"].values, index=norm(uh["vid"]))
    m = g["source"] == "upper_hutt"
    g.loc[m, "lv"] = norm(g.loc[m, "vid"]).map(uh).values
    # Keep one source per rating unit (layers overlap at council boundaries).
    g = g[g["lv"].notna()]
    parcels = spread(g)
    parcels.to_parquet(CACHE)
    return parcels


def main():
    FIGS.mkdir(parents=True, exist_ok=True)
    parcels = gpd.read_parquet(CACHE) if CACHE.exists() else build()
    print(f"{len(parcels):,} parcels, land value ${parcels['lv'].sum() / 1e9:,.1f}bn")

    ta = gpd.read_file(RAW / "ta_2025.geojson").to_crs(2193)
    region = ta[ta["rc_name"] == "Wellington Region"]
    outline = region.dissolve()

    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    colours = [cmap(i / (len(LABELS) - 1)) for i in range(len(LABELS))]
    idx = np.clip(np.digitize(parcels["lv_m2"], BINS) - 1, 0, len(LABELS) - 1)
    parcels["colour"] = [colours[i] for i in idx]

    fig = plt.figure(figsize=(16, 9), facecolor=SURFACE)
    panels = [
        (fig.add_axes([0.005, 0.02, 0.52, 0.84]), region.total_bounds, "Wellington Region"),
        (fig.add_axes([0.53, 0.02, 0.465, 0.84]), (1_740_000, 5_418_500, 1_768_000, 5_446_500),
         "Wellington, Hutt Valley and Porirua"),
    ]
    for ax, (xmin, ymin, xmax, ymax), label in panels:
        ax.set_facecolor(SURFACE)
        outline.plot(ax=ax, color=GRID, linewidth=0)
        parcels.plot(ax=ax, color=parcels["colour"], linewidth=0)
        region.boundary.plot(ax=ax, color="#ffffff", linewidth=0.6)
        pad = 0.02 * (xmax - xmin)
        ax.set_xlim(xmin - pad, xmax + pad)
        ax.set_ylim(ymin - pad, ymax + pad)
        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.set_title(label, loc="left", fontsize=11, color=INK2)
    ax = panels[0][0]
    for c, lab in zip(colours, LABELS):
        ax.scatter([], [], marker="s", s=70, color=c, label=lab)
    ax.scatter([], [], marker="s", s=70, color=GRID, label="No rateable land value (roads, reserves)")
    ax.legend(title="Land value per m² of parcel", loc="lower right", bbox_to_anchor=(1.0, 0.0), frameon=False,
              fontsize=9, title_fontsize=9.5)
    fig.text(0.01, 0.97, "Land value across the Wellington Region", fontsize=15, fontweight="bold",
             color=INK, va="top")
    fig.text(0.01, 0.93, f"Council rating valuations (2023–2025 revaluations) for {len(parcels):,} parcels, "
             f"${parcels['lv'].sum() / 1e9:,.0f}bn of land. Upper Hutt land value estimated from capital value.",
             fontsize=10, color=INK2, va="top")
    out = FIGS / "map_wellington_region_land_value.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    print(f"wrote {out}")

    by_ta = gpd.sjoin(parcels.assign(geometry=parcels.representative_point())[["lv", "area", "geometry"]],
                      region[["ta_name", "geometry"]], predicate="within").groupby("ta_name")[["lv", "area"]].sum()
    by_ta["lv_bn"] = by_ta["lv"] / 1e9
    by_ta["mean_lv_per_m2_private_land"] = by_ta["lv"] / by_ta["area"]
    print(by_ta[["lv_bn", "mean_lv_per_m2_private_land"]].round(1).sort_values("lv_bn", ascending=False).to_string())


if __name__ == "__main__":
    main()
