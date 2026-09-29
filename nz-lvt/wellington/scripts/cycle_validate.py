"""Validate and calibrate the cycling model's route choice against WCC's transport sensor counts.

Counts: WCC transport sensors (VivaCity), hourly cyclists per countline and direction, from the
public S3 bucket (fetch_cycling.py --extras). For each countline and direction: average weekday
cyclists over the last 12 months (cycle and road countlines, and pedestrian countlines on shared
paths), on days the sensor was available >= 95% of the time (daily
availability file); countlines need >= 20 such days. Countlines labelled "crossing" or "entrance" are left out (they count people crossing
a road or entering a site, not riding along a link).

Matching: each countline (a short line across a path or lane) is matched to the network edges it
crosses (or the nearest within 15 m). Each counted compass direction is matched to the model's
flow in the arc direction closest to that bearing, so one-sided countlines ("RHS", "LHS") are
compared with one direction only.

Calibration: re-run the model's census scenario (people who cycled to work or study in 2023)
over a grid of route-choice parameters (stress factor for level 3 and 4 streets, climb weight,
crossing penalties) and keep the set with the best agreement: Pearson correlation of
log(1 + count) and log(1 + modelled). The ratio counted / modelled is the factor from census
commuting to all-day weekday cycling at these sites.

Writes outputs/tables/cycle_counters.csv (per countline and direction), cycle_calibration.json
(grid results and the best parameters, read by cycle_model.py) and cycle_calibration_grid.csv.
"""

import itertools
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

import cycle_model as M

ROOT = Path(__file__).resolve().parents[1]
SENS = ROOT / "data" / "raw" / "cycling" / "sensors"
TABLES = ROOT / "outputs" / "tables"
COMPASS = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
GRID = dict(stress=[(1.1, 1.25), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 10.0)], climb=[5.0, 10.0, 20.0],
            cross=[(30.0, 80.0), (150.0, 400.0)])


def counts(min_avail=95, min_days=20):
    """Average weekday cyclists per countline and direction over the last 12 months.

    A weekday is valid when the countline's sensor (viewpoint) was available >= 95% of the day.
    Hours with no cyclists are often absent from the file, so on valid days missing hours and
    directions count as zero."""
    c = pd.read_csv(SENS / "cyclist.csv", parse_dates=["COUNTLINE_DATE"])
    meta = pd.read_csv(SENS / "meta.csv")
    av = pd.read_csv(SENS / "availability.csv", parse_dates=["AVAILABILITY_DATE"])
    end = c["COUNTLINE_DATE"].max()
    av = av[(av["AVAILABILITY_DATE"] > end - pd.Timedelta(days=365)) & (av["AVAILABILITY_DATE"] <= end)
            & (av["AVAILABILITY_DATE"].dt.dayofweek < 5) & (av["DAILY_PERCENTAGE"] >= min_avail)]
    valid = meta[["COUNTLINE_ID", "VIEWPOINT_ID"]].merge(av, on="VIEWPOINT_ID")[["COUNTLINE_ID", "AVAILABILITY_DATE"]]
    valid = valid.rename(columns={"AVAILABILITY_DATE": "COUNTLINE_DATE"})
    days = valid.groupby("COUNTLINE_ID").size().rename("days")
    c = c.merge(valid, on=["COUNTLINE_ID", "COUNTLINE_DATE"])
    tot = c.groupby(["COUNTLINE_ID", "DIRECTION"])["DIRECTION_COUNT"].sum().rename("total").reset_index()
    # Every direction the countline records, including ones with no cyclists.
    dirs = pd.concat([meta[["COUNTLINE_ID", "DIRECTION_IN"]].rename(columns={"DIRECTION_IN": "DIRECTION"}),
                      meta[["COUNTLINE_ID", "DIRECTION_OUT"]].rename(columns={"DIRECTION_OUT": "DIRECTION"})]).dropna()
    out = dirs.merge(tot, on=["COUNTLINE_ID", "DIRECTION"], how="left").fillna({"total": 0})
    out = out.merge(days.reset_index(), on="COUNTLINE_ID")
    out["weekday_avg"] = out["total"] / out["days"]
    out = out.merge(meta, on="COUNTLINE_ID")
    out = out[(out["days"] >= min_days) & ~out["NAME"].str.contains("crossing|entrance", case=False, na=False)]
    # Pedestrian countlines on footpaths count cyclists who can't be placed on a network link;
    # keep them only where they sit on a path, walkway or cycleway that bikes share.
    ped = out["PRIMARY_TRANSPORT_CLASS"] == "Pedestrian"
    shared = out["NAME"].str.contains("path|cycle|walkway|promenade|ara ", case=False, na=False)
    out = out[~ped | shared]
    geom = [LineString([(a, b), (c_, d)]) for a, b, c_, d in zip(out["LONGITUDE_START_LINE"], out["LATITUDE_START_LINE"],
                                                                 out["LONGITUDE_END_LINE"], out["LATITUDE_END_LINE"])]
    return gpd.GeoDataFrame(out.reset_index(drop=True), geometry=geom, crs=4326).to_crs(2193)


def bearing(line, at):
    """Bearing (deg from north) of a line's direction near point `at`."""
    d = line.project(at)
    a = line.interpolate(max(d - 5, 0))
    b = line.interpolate(min(d + 5, line.length))
    return (np.degrees(np.arctan2(b.x - a.x, b.y - a.y)) + 360) % 360


def match(cl, e):
    """For each countline direction: list of (edge, arc_dir) whose flows it should equal."""
    lines = cl.drop_duplicates("COUNTLINE_ID").set_index("COUNTLINE_ID")
    hit = gpd.sjoin(gpd.GeoDataFrame(geometry=lines.geometry.buffer(1.5), index=lines.index, crs=2193),
                    e[["geometry"]], predicate="intersects")
    edges = hit.groupby(level=0)["index_right"].apply(list)
    miss = lines.index.difference(edges.index)
    if len(miss):
        near = gpd.sjoin_nearest(gpd.GeoDataFrame(geometry=lines.loc[miss].geometry.centroid, index=miss, crs=2193),
                                 e[["geometry"]], max_distance=15)
        edges = pd.concat([edges, near.groupby(level=0)["index_right"].apply(list)])
    rows = []
    for _, r in cl.iterrows():
        if r["COUNTLINE_ID"] not in edges.index or r["DIRECTION"] not in COMPASS:
            continue
        want = COMPASS[r["DIRECTION"]]
        mid = lines.loc[r["COUNTLINE_ID"]].geometry.centroid
        pairs = []
        for ei in edges[r["COUNTLINE_ID"]]:
            b = bearing(e.geometry.loc[ei], mid)
            diff = min(abs(b - want), 360 - abs(b - want))
            pairs.append((ei, 0 if diff <= 90 else 1))
        rows.append(dict(COUNTLINE_ID=r["COUNTLINE_ID"], DIRECTION=r["DIRECTION"], pairs=pairs))
    return pd.DataFrame(rows)


def modelled(m, arcs, arc_flow, e):
    key = pd.Series(arc_flow[:, 0], index=pd.MultiIndex.from_arrays([arcs["edge"].values, arcs["dir"].values]))
    key = key.groupby(level=[0, 1]).sum()
    return np.array([sum(key.get((ei, d), 0.0) for ei, d in p) for p in m["pairs"]])


def score(obs, mod, min_count=10):
    ok = obs >= min_count
    x, y = np.log1p(mod[ok]), np.log1p(obs[ok])
    r = np.corrcoef(x, y)[0, 1]
    k = obs[ok].sum() / max(mod[ok].sum(), 1e-9)
    from scipy.stats import spearmanr
    rho = spearmanr(obs[ok], mod[ok]).statistic
    return dict(n=int(ok.sum()), log_r=float(r), spearman=float(rho), k=float(k))


def main():
    cl = counts()
    e = M.gpd.read_parquet(M.PROC / "cycle_edges.parquet")
    m = match(cl, e)
    obs = cl.set_index(["COUNTLINE_ID", "DIRECTION"])["weekday_avg"].reindex(
        pd.MultiIndex.from_frame(m[["COUNTLINE_ID", "DIRECTION"]])).values
    print(f"countline-directions matched: {len(m):,} ({(obs >= 10).sum()} with >= 10 cyclists/weekday)")
    results, setup, best = [], None, None
    for (s3, s4), climb, (c3, c4) in itertools.product(GRID["stress"], GRID["climb"], GRID["cross"]):
        p = dict(climb=climb, stress={2: 1.0, 3: s3, 4: s4}, cross={3: c3, 4: c4})
        flow, arc_flow, _, arcs, _, setup = M.run(p, scenarios=("census",), verbose=False, setup=setup)
        mod = modelled(m, arcs, arc_flow, e)
        sc = score(obs, mod)
        results.append(dict(stress3=s3, stress4=s4, climb=climb, cross3=c3, cross4=c4, **sc))
        print(f"  stress {s3}/{s4} climb {climb} cross {c3}/{c4}: log r {sc['log_r']:.3f}, "
              f"spearman {sc['spearman']:.3f}, k {sc['k']:.2f}", flush=True)
        if best is None or sc["log_r"] > best[0]["log_r"]:
            best = (sc, p, mod)
    grid = pd.DataFrame(results).sort_values("log_r", ascending=False)
    grid.round(3).to_csv(TABLES / "cycle_calibration_grid.csv", index=False)
    sc, p, mod = best
    default = grid[(grid.stress3 == 1.1) & (grid.climb == 10.0) & (grid.cross3 == 30.0)].iloc[0].to_dict()
    (TABLES / "cycle_calibration.json").write_text(json.dumps(dict(
        best=dict(climb=p["climb"], stress=p["stress"], cross=p["cross"], **sc),
        default=default, grid=GRID), indent=1, default=float))
    out = cl.drop(columns="geometry").set_index(["COUNTLINE_ID", "DIRECTION"]).loc[
        list(zip(m["COUNTLINE_ID"], m["DIRECTION"]))].reset_index()
    out["modelled_census"] = mod
    out["edges"] = [";".join(f"{a}:{b}" for a, b in pr) for pr in m["pairs"]]
    out[["COUNTLINE_ID", "NAME", "PRIMARY_TRANSPORT_CLASS", "DIRECTION", "days", "weekday_avg", "modelled_census",
         "edges"]].round(1).to_csv(TABLES / "cycle_counters.csv", index=False)
    print("\nbest:", p, sc)
    print("default:", {k: round(v, 3) if isinstance(v, float) else v for k, v in default.items()})


if __name__ == "__main__":
    main()
