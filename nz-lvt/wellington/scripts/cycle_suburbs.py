"""Suburb report cards for the Wellington cycle network: how much of each suburb's potential
cycling has a low-stress route, what blocks the rest, and which candidate projects would help.

Builds on cycle_priorities.py (same trip table, connectivity standard and candidate list); run
that first so outputs/blocks/cycle_priorities.geojson exists. Does not change any other output.

1. Trips. data/processed/cycle_od_matrix.npz: weekday outbound trips from each home node to each
   destination node (all purposes). Pairs under 200 m are left out, as in cycle_priorities. Each
   home node is assigned to the suburb of its nearest property (data/processed/units.parquet);
   destination nodes within 250 m of a property get that property's suburb. The CBD is Wellington
   Central, Te Aro and Pipitea.
2. Connectivity. A trip is connected when its least-effort route on the low-stress network is at
   most 1.25 x the effort of its least-effort route on the whole network (effort = metres + 30 x
   metres climbed; first and last arcs up to 150 m may be any stress). Headline standard "all
   ages" (stress 1 links, crossings of stress <= 2); secondary "confident" (stress <= 2). Uses
   cycle_priorities.Connectivity unchanged. Percentages are of Go Dutch trips that are in the
   pair table (200 m or more, reachable).
3. Per suburb (>= 200 Go Dutch trips a weekday): Go Dutch trips (the potential with a Dutch-
   quality network), census trips (people cycling today), % connected at each standard, % of
   trips to the CBD connected (all ages), and the trip-weighted median effort (km) of its trips.
4. Blockers. For Go Dutch trips that are not connected at all ages, the least-effort route on
   the whole network is traced (Dijkstra predecessors from every home node). An arc is a blocker
   when its stress is 2 or more, or the crossing at its end is stress 3 or more (arcs that are
   the access leg at a trip end are ignored). Stress arcs are credited to their own street; a
   blocked crossing to the busiest other street meeting at that junction (the road being
   crossed). Each trip is counted once per street (where it first meets a blocker on that street), and
   trips affected are summed by street name. The top three named streets are reported (unnamed connectors are skipped). A trip that
   meets several blockers counts towards each, so these do not add up to the unconnected total.
   Route flows are exact: trips through an arc are summed up the shortest-path tree of each home.
5. Projects. Candidates in cycle_priorities.geojson that intersect the suburb's properties
   (buffered 200 m) or are named as one of its top blockers, ordered by rank (Go Dutch trips
   newly connected per $M, with the network bonus). Top three (one per street name) listed with treatment and
   indicative cost (not a benefit-cost ratio). Candidates near a boundary can appear for
   both neighbouring suburbs.

Writes outputs/tables/cycle_suburbs.csv and outputs/blocks/cycle_suburbs.geojson (WGS84; one
point per suburb, all fields plus a plain-text `card`).
"""

import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

import cycle_priorities as CP

PROC, TABLES, BLOCKS = CP.PROC, CP.TABLES, CP.BLOCKS
OUTSIDE, OUTSIDE_M = "Outside the city", 400  # origins over 400 m from any city property
CBD = ["Wellington Central", "Te Aro", "Pipitea"]
MIN_TRIPS = 200
DEST_M = 250
AREA_BUFFER_M = 200
CHUNK = 400
CAP = 1.25
GO, CEN = CP.SCEN.index("godutch"), CP.SCEN.index("census")


def wmedian(x, w):
    o = np.argsort(x)
    c = np.cumsum(w[o])
    return float(x[o][np.searchsorted(c, c[-1] / 2)])


def pair_table(a, n, S, D, mat, allowed):
    """All origin-destination pairs with trips and length >= 200 m, with full-network length and
    effort, built in chunks of origins (pairs come out sorted by origin row)."""
    g_len, g_eff = CP.csr(a, allowed, n), CP.csr(a, allowed, n, "eff")
    out = {k: [] for k in ("si", "ti", "len", "eff", "wg", "wc")}
    for c0 in range(0, len(S), CHUNK):
        c1 = min(c0 + CHUNK, len(S))
        m = mat[c0:c1]
        r, ti = np.nonzero(m.sum(axis=2) > 0)
        dl = dijkstra(g_len, indices=S[c0:c1])[:, D][r, ti]
        ok = np.isfinite(dl) & (dl >= CP.MIN_M)
        r, ti, dl = r[ok], ti[ok], dl[ok]
        de = dijkstra(g_eff, indices=S[c0:c1])[:, D][r, ti]
        out["si"].append((r + c0).astype(np.int32))
        out["ti"].append(ti.astype(np.int32))
        out["len"].append(dl.astype(np.float32))
        out["eff"].append(de.astype(np.float32))
        out["wg"].append(m[r, ti, GO].astype(np.float32))
        out["wc"].append(m[r, ti, CEN].astype(np.float32))
    return {k: np.concatenate(v) for k, v in out.items()}


def connected_flags(a, n, S, D, pt, tol):
    """Connected (bool per pair) at cap 1.25 for one standard; frees the distance matrices."""
    P = dict(si=pt["si"], t=D[pt["ti"]], km=pt["len"] / 1000)
    C = CP.Connectivity(a, n, S, P, None, {"eff": pt["eff"], "len": pt["len"]}, tol)
    flag = C.d <= CAP * C.d_full
    access = C.access.copy()
    del C
    return flag, access


def street_keys(a, e):
    """Per arc: street name id (base), and blocker id (-1 when the arc is not a blocker; stress
    arcs take their own street, blocked crossings the busiest other street at the junction)."""
    name = e["name"].fillna("(unnamed link)").astype(str)
    names = pd.Index(name.unique())
    edge_name = names.get_indexer(name)
    arc_name = edge_name[a["edge"].values]
    stress = a["lts"].values >= 2
    xing = (a["cross"].values >= 3) & ~stress
    bid = np.full(len(a), -1, dtype=np.int64)
    bid[stress] = arc_name[stress]
    # Busiest other street at the arc's end node (the road being crossed).
    top = a[["u", "edge", "lts"]].assign(nm=arc_name)
    top = top[top["lts"] < CP.BARRED].sort_values(["u", "lts"], ascending=[True, False])
    top = top.groupby("u").head(3)
    if xing.any():
        cand = a.loc[xing, ["v", "edge"]].reset_index().rename(columns={"index": "arc"})
        j = cand.merge(top, left_on="v", right_on="u", suffixes=("", "_o"))
        j = j[j["edge"] != j["edge_o"]].sort_values(["arc", "lts"], ascending=[True, False])
        j = j.drop_duplicates("arc").set_index("arc")
        bid[j.index.values] = j["nm"].values
        # Junction with no other named street: credit the arc's own street.
        rest = np.flatnonzero(xing & (bid < 0))
        bid[rest] = arc_name[rest]
    return names, arc_name, bid


def trace_blockers(a, n, S, pt, unconn, sub_o, nsub, access):
    """Trips (per suburb x blocker street) whose whole-network route meets a blocker, from exact
    subtree flows of each origin's shortest-path tree."""
    e = gpd.read_parquet(PROC / "cycle_edges.parquet", columns=["name", "geometry"])
    names, arc_name, bid = street_keys(a, e)
    bid = np.where(access, -1, bid)
    allowed = a["lts"].values < CP.BARRED
    s = a.loc[allowed, ["u", "v", "eff"]].copy()
    s["arc"] = s.index
    s = s.sort_values("eff").drop_duplicates(["u", "v"])
    key = s["u"].values.astype(np.int64) * n + s["v"].values
    o = np.argsort(key)
    key, arc_of = key[o], s["arc"].values[o]
    g = CP.csr(a, allowed, n, "eff")
    nb = len(names)
    tally = np.zeros(nsub * nb)
    si = pt["si"]
    for c0 in range(0, len(S), CHUNK):
        c1 = min(c0 + CHUNK, len(S))
        lo, hi = np.searchsorted(si, [c0, c1])
        sel = unconn[lo:hi]
        if not sel.any():
            continue
        r = len(range(c0, c1))
        flat = (si[lo:hi][sel] - c0).astype(np.int64) * (n + 1) + pt["t"][lo:hi][sel]
        flow = np.bincount(flat, weights=pt["wg"][lo:hi][sel], minlength=r * (n + 1)).reshape(r, n + 1)
        dist, pred = dijkstra(g, indices=S[c0:c1], return_predecessors=True)
        psafe = np.where(pred < 0, n, pred)
        order = np.argsort(-dist, axis=1)
        rows = np.arange(r)
        for j in range(n):  # children before parents: farthest first
            v = order[:, j]
            p = psafe[rows, v]
            flow[rows, p] += flow[rows, v]
        flow = flow[:, :n]
        # Arc into each node on the tree, and the blocker id of the arc before it.
        vv = np.broadcast_to(np.arange(n), (r, n))
        k = np.where(pred >= 0, pred.astype(np.int64) * n + vv, -1)
        pos = np.clip(np.searchsorted(key, k), 0, len(key) - 1)
        arc_in = np.where((k >= 0) & (key[pos] == k), arc_of[pos], -1)
        b_in = np.where(arc_in >= 0, bid[np.maximum(arc_in, 0)], -1)
        # Blocker street last met on the way to each node (nearest parent first), so a trip counts
        # once per street even when access legs or side arcs interrupt the run.
        carry = np.full((r, n + 1), -1, dtype=np.int64)
        for j in range(n - 1, -1, -1):
            v = order[:, j]
            carry[rows, v] = np.where(b_in[rows, v] >= 0, b_in[rows, v], carry[rows, psafe[rows, v]])
        prev = np.take_along_axis(carry, psafe, axis=1)
        hit = (b_in >= 0) & (b_in != prev) & (flow > 1e-9)
        rr, vc = np.nonzero(hit)
        sb = sub_o[c0 + rr]
        keep = sb >= 0
        idx = sb[keep] * nb + b_in[rr[keep], vc[keep]]
        tally += np.bincount(idx, weights=flow[rr[keep], vc[keep]], minlength=nsub * nb)
        print(f"  traced origins {c1:,}/{len(S):,}", flush=True)
    return names, tally.reshape(nsub, nb)


def money(x):
    return f"${x:.2f}M" if x < 1 else f"${x:.1f}M"


def join_names(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def main():
    t0 = time.time()
    e = gpd.read_parquet(CP.PROC / "cycle_edges.parquet")
    nodes = pd.read_parquet(PROC / "cycle_nodes.parquet")
    n = len(nodes)
    xy = nodes[["x", "y"]].values
    a = CP.arc_table(e)
    S, D, mat = CP.od_matrix()
    allowed = a["lts"].values < CP.BARRED

    # Suburbs of home nodes and destination nodes.
    units = gpd.read_parquet(PROC / "units.parquet", columns=["Suburb", "geometry"]).to_crs(2193)
    subs = np.array(sorted(units["Suburb"].dropna().unique()))
    sub_id = pd.Index(subs).get_indexer(units["Suburb"])
    tree = cKDTree(np.column_stack([units.geometry.centroid.x, units.geometry.centroid.y]))
    dist_o, i_o = tree.query(xy[S])
    # Homes outside the city (Lower Hutt, Porirua) snap to the city's edge nodes; they are not
    # Horokiwi's or Takapu Valley's residents, so pool them as "Outside the city".
    subs = np.append(subs, OUTSIDE)
    sub_o = np.where(dist_o > OUTSIDE_M, len(subs) - 1, sub_id[i_o])
    dist_d, i_d = tree.query(xy[D])
    sub_d = np.where(dist_d <= DEST_M, sub_id[i_d], -1)
    cbd_id = [int(np.flatnonzero(subs == s)[0]) for s in CBD]
    cbd_d = np.isin(sub_d, cbd_id)
    print(f"origins {len(S):,} (median {np.median(dist_o):.0f} m to nearest property); "
          f"{len(subs)} suburbs; CBD destination nodes {cbd_d.sum()}")

    pt = pair_table(a, n, S, D, mat, allowed)
    pt["t"] = D[pt["ti"]]
    print(f"pairs {len(pt['si']):,}; Go Dutch trips {pt['wg'].sum():,.0f} of {mat[:, :, GO].sum():,.0f} "
          f"({time.time() - t0:.0f}s)", flush=True)
    conn = {}
    for tol in ("all_ages", "confident"):
        conn[tol], acc = connected_flags(a, n, S, D, pt, tol)
        print(f"  {tol}: {(pt['wg'] * conn[tol]).sum() / pt['wg'].sum() * 100:.1f}% of Go Dutch trips connected "
              f"({time.time() - t0:.0f}s)", flush=True)
        if tol == "all_ages":
            access = acc

    ns = len(subs)
    so = sub_o[pt["si"]]
    wg, wc = pt["wg"].astype(np.float64), pt["wc"].astype(np.float64)
    to_cbd = cbd_d[pt["ti"]]
    bc = lambda m, w: np.bincount(so[m], weights=w[m], minlength=ns)  # noqa: E731
    allm = np.ones(len(so), bool)
    res = pd.DataFrame({
        "suburb": subs,
        "trips_godutch": np.bincount(sub_o, weights=mat[:, :, GO].sum(axis=1), minlength=ns),
        "trips_census": np.bincount(sub_o, weights=mat[:, :, CEN].sum(axis=1), minlength=ns),
    })
    cov = bc(allm, wg)
    res["pct_all_ages"] = bc(conn["all_ages"], wg) / np.maximum(cov, 1e-9) * 100
    res["pct_confident"] = bc(conn["confident"], wg) / np.maximum(cov, 1e-9) * 100
    res["trips_to_cbd"] = bc(to_cbd, wg)
    res["pct_cbd_all_ages"] = np.where(res["trips_to_cbd"] > 0,
                                       bc(to_cbd & conn["all_ages"], wg) / np.maximum(res["trips_to_cbd"], 1e-9) * 100,
                                       np.nan)
    res["unconnected_trips"] = bc(~conn["all_ages"], wg)
    med_e, med_l = [], []
    for k in range(ns):
        m = (so == k) & (wg > 0)
        med_e.append(wmedian(pt["eff"][m], wg[m]) / 1000 if m.any() else np.nan)
        med_l.append(wmedian(pt["len"][m], wg[m]) / 1000 if m.any() else np.nan)
    res["median_effort_km"], res["median_dist_km"] = med_e, med_l
    res["n_homes"] = np.bincount(sub_o, minlength=ns)

    # Blockers.
    pt_unconn = ~conn["all_ages"] & (pt["wg"] > 0)
    names, tally = trace_blockers(a, n, S, pt, pt_unconn, sub_o, ns, access)
    for k in (1, 2, 3):
        res[f"blocker{k}"], res[f"blocker{k}_trips"] = "", 0.0
    tally[:, names.get_loc("(unnamed link)")] = 0  # connectors with no street name can't be reported
    for i in range(ns):
        top = np.argsort(-tally[i])[:3]
        for k, j in enumerate(top, 1):
            if tally[i, j] >= 1:
                res.loc[i, f"blocker{k}"], res.loc[i, f"blocker{k}_trips"] = names[j], tally[i, j]
    print(f"traced ({time.time() - t0:.0f}s)")

    # Projects near each suburb.
    prj = gpd.read_file(BLOCKS / "cycle_priorities.geojson").to_crs(2193).sort_values("rank")
    units["geometry"] = units.geometry.make_valid()
    out = res[res["suburb"] == OUTSIDE].iloc[0]
    print(f"outside the city: {out['trips_godutch']:,.0f} Go Dutch trips, {out['pct_all_ages']:.1f}% all-ages connected")
    res = res[res["suburb"] != OUTSIDE].reset_index(drop=True)
    area = units.dissolve(by="Suburb")
    rep = gpd.GeoSeries(area.geometry.representative_point(), crs=2193)
    buf = area.geometry.buffer(AREA_BUFFER_M)
    psindex = prj.sindex
    for k in (1, 2, 3):
        res[f"project{k}"], res[f"project{k}_treatment"], res[f"project{k}_cost_m"], res[f"project{k}_rank"] = \
            "", "", np.nan, np.nan
    for i, s in enumerate(res["suburb"]):
        hit = set(prj.index[psindex.query(buf[s], predicate="intersects")])
        hit |= set(prj.index[prj["name"].isin([b for b in res.loc[i, ["blocker1", "blocker2", "blocker3"]] if b])])
        near = prj.loc[sorted(hit, key=lambda j: prj.at[j, "rank"])].drop_duplicates("name").head(3)
        for k, (_, p) in enumerate(near.iterrows(), 1):
            res.loc[i, f"project{k}"], res.loc[i, f"project{k}_treatment"] = p["name"], p["treatment"]
            res.loc[i, f"project{k}_cost_m"], res.loc[i, f"project{k}_rank"] = p["cost"], p["rank"]

    res = res[res["trips_godutch"] >= MIN_TRIPS].sort_values("trips_godutch", ascending=False).reset_index(drop=True)
    res["card"] = [card(r) for _, r in res.iterrows()]
    num = res.select_dtypes("number").columns
    out = res.copy()
    out[num] = out[num].round(2)
    out.to_csv(TABLES / "cycle_suburbs.csv", index=False)
    g = gpd.GeoDataFrame(out, geometry=rep.reindex(out["suburb"]).values, crs=2193).to_crs(4326)
    g.to_file(BLOCKS / "cycle_suburbs.geojson", driver="GeoJSON")

    pd.set_option("display.width", 250)
    cols = ["suburb", "trips_godutch", "trips_census", "pct_all_ages", "pct_confident", "pct_cbd_all_ages",
            "median_effort_km", "blocker1", "blocker2", "blocker3", "project1"]
    print(res[cols].round(1).to_string(index=False))
    print(f"{len(res)} suburbs; done in {time.time() - t0:.0f}s")


def card(r):
    s1 = (f"{r['pct_all_ages']:.0f}% of potential trips from {r['suburb']} ({r['trips_godutch']:,.0f} a weekday) have "
          f"an all-ages route; {r['pct_confident']:.0f}% do for confident riders")
    if r["trips_to_cbd"] >= 50 and r["suburb"] not in CBD:
        s1 += f", and {r['pct_cbd_all_ages']:.0f}% of trips to the CBD do"
    s1 += "."
    bl = [r[f"blocker{k}"] for k in (1, 2, 3) if r[f"blocker{k}"]]
    s2 = (f"The main blockers are {join_names(bl)}." if bl else
          "Almost every potential trip already has an all-ages route.")
    pr = [f"{r[f'project{k}']} ({r[f'project{k}_treatment'].lower()}, about {money(r[f'project{k}_cost_m'])})"
          for k in (1, 2, 3) if r[f"project{k}"]]
    s3 = f"Best-value fixes: {'; '.join(pr)}." if pr else "No candidate project lies in or near it."
    return " ".join([s1, s2, s3])


if __name__ == "__main__":
    main()
