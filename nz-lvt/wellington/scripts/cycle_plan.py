"""A network plan for Wellington City: rider types, value in the complete network, and packages.

cycle_priorities.py scores projects one at a time from today's network, so links that only pay
off together (a suburb's route to town through three gaps) score near zero. This script looks at
the complete network instead, and at who would ride.

1. Rider types (a synthetic population in the spirit of agent-based models). Potential riders
   on each trip (the Go Dutch scenario) are split into the Portland types (Dill & McNeil 2013;
   shares of those willing at all): strong and fearless 6% (ride anything), enthused and
   confident 13% (need a "confident" route: stress <= 2), interested but concerned 81% (need an
   all-ages route: stress 1 with safe crossings). A type rides a trip when it has a route at its
   standard within 1.25x the least effort (effort = metres + 30 x metres climbed). Expected
   riders on a trip = Go Dutch trips x (0.06 + 0.13 x confident-connected + 0.81 x
   all-ages-connected), never below today's cycling (census).
2. Complete network. Every candidate from cycle_priorities.py (gap corridors, council-plan links,
   crossings) is built at once. Trips that gain riders are traced along their all-ages route in
   the complete network (least effort), and each candidate is credited with the extra riders on
   trips that use it: "relied on by" (full credit to each candidate on the route) and a shared
   credit (split by the candidate's share of the route's new-infrastructure length).
3. Packages. For each suburb, the candidates that carry at least 25% of its extra riders in the
   complete network form its package (the gaps its trips need closed together). Each package is
   evaluated exactly from today's network (extra expected riders), costed, and ranked by riders
   per $M. A greedy order over packages then re-scores after each is built.

Writes outputs/tables/cycle_plan_candidates.csv, cycle_plan_packages.csv, cycle_plan_summary.json
and outputs/blocks/cycle_plan_packages.geojson (WGS84).
"""

import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

import cycle_model as M
import cycle_priorities as P

TYPES = dict(fearless=0.06, confident=0.13, concerned=0.81)
PKG_SHARE = 0.25
MIN_SUBURB_GAIN = 20  # extra riders/weekday for a suburb package
N_GREEDY = 12


def tree_paths(pred, src, attrs, keys, key_arc, n):
    """As cycle_model.tree_paths, with the arc into each node looked up from sorted (u, v) keys."""
    has = pred >= 0
    arc = np.full(len(pred), -1)
    idx = np.searchsorted(keys, pred[has].astype(np.int64) * n + np.where(has)[0])
    arc[has] = key_arc[idx]
    k = attrs.shape[1]
    V = np.zeros((len(pred), k + 1))
    V[has, :k] = attrs[arc[has]]
    V[has, k] = 1
    p = np.where(has, pred, np.arange(len(pred)))
    p[src] = src
    while not (p == p[p]).all():
        V = V + V[p]
        p = p[p]
    return arc, V[:, :k], V[:, k].astype(int)


def riders(w_gd, w_cen, conn_conf, conn_all):
    r = w_gd * (TYPES["fearless"] + TYPES["confident"] * conn_conf + TYPES["concerned"] * conn_all)
    return np.maximum(r, w_cen)


def main():
    t0 = time.time()
    e = gpd.read_parquet(P.PROC / "cycle_edges.parquet")
    nodes = pd.read_parquet(P.PROC / "cycle_nodes.parquet")
    n = len(nodes)
    a = P.arc_table(e)
    S, D, mat = P.od_matrix()
    allowed = np.ones(len(a), bool)
    d_len = dijkstra(P.csr(a, allowed, n), indices=S)[:, D].astype(np.float32)
    si, ti = np.nonzero(mat[:, :, 1] > 0)
    df = d_len[si, ti]
    ok = np.isfinite(df) & (df >= P.MIN_M)
    si, ti, df = si[ok], ti[ok], df[ok]
    del d_len
    e_full = dijkstra(P.csr(a, allowed, n, "eff"), indices=S)[:, D].astype(np.float32)[si, ti]
    full = dict(len=df, eff=e_full)
    w = mat[si, ti].astype(np.float64)
    w_cen, w_gd = w[:, 0], w[:, 1]
    Pp = dict(si=si, t=D[ti], km=df / 1000)
    print(f"pairs {len(si):,}; Go Dutch trips {w_gd.sum():,.0f} ({time.time() - t0:.0f}s)", flush=True)

    cand, _ = P.candidates(a)
    print(f"candidates: {len(cand)} ({time.time() - t0:.0f}s)", flush=True)
    C = {lv: P.Connectivity(a, n, S, Pp, w, full, lv) for lv in ("all_ages", "confident")}
    cap = 1.25
    now = {lv: (C[lv].d <= cap * C[lv].d_full) for lv in C}
    r_now = riders(w_gd, w_cen, now["confident"], now["all_ages"])

    # Complete network: every candidate built.
    idx_l = np.unique(np.concatenate(cand["idx_l"].values)).astype(int)
    idx_c = np.unique(np.concatenate(cand["idx_c"].values)).astype(int)
    # (with_arcs' shortcut suits a few added arcs; for everything, rebuild the graphs directly.)
    after = {}
    lts_all, cross_all = a["lts"].values.copy(), a["cross"].values.copy()
    lts_all[idx_l] = np.minimum(lts_all[idx_l], 1)
    cross_all[idx_c] = np.minimum(cross_all[idx_c], 2)
    for lv, c in C.items():
        mask = P.low_stress(a, lv, lts_all, cross_all, c.access)
        dS = dijkstra(P.csr(a, mask, n, "eff"), indices=S).astype(np.float32)
        after[lv] = dS[si, Pp["t"]] <= cap * c.d_full
        del dS
    r_full = riders(w_gd, w_cen, after["confident"], after["all_ages"])
    gain = r_full - r_now
    print(f"expected riders/weekday (outbound): census {w_cen.sum():,.0f}; today's network {r_now.sum():,.0f}; "
          f"complete network {r_full.sum():,.0f}; Go Dutch ceiling {w_gd.sum():,.0f} ({time.time() - t0:.0f}s)",
          flush=True)

    # Trace gaining trips on the complete all-ages network and credit candidates.
    arc_cand = np.full(len(a), -1)
    arc_len = a["len"].values
    for k, r in cand.iterrows():
        arc_cand[r["idx_l"]] = k
        # a crossing treatment counts on the arc arriving at the crossing
        arc_cand[r["idx_c"][arc_cand[r["idx_c"]] < 0]] = k
    mask = P.low_stress(a, "all_ages", lts_all, cross_all, C["all_ages"].access)
    sub = a[mask].sort_values("eff").drop_duplicates(["u", "v"])
    G = csr_matrix((sub["eff"].values, (sub["u"].values, sub["v"].values)), shape=(n, n))
    order_k = np.argsort(sub["u"].values.astype(np.int64) * n + sub["v"].values)
    keys = (sub["u"].values.astype(np.int64) * n + sub["v"].values)[order_k]
    key_arc = sub.index.values[order_k]
    cand_len = np.where(arc_cand >= 0, arc_len, 0.0)
    attrs = cand_len[:, None]
    relied = np.zeros(len(cand))
    shared = np.zeros(len(cand))
    units = gpd.read_parquet(P.PROC / "units.parquet")[["Suburb", "geometry"]]
    upt = gpd.GeoDataFrame(units[["Suburb"]], geometry=units.geometry.representative_point(), crs=2193)
    onode = gpd.GeoDataFrame(geometry=gpd.points_from_xy(nodes.loc[S, "x"], nodes.loc[S, "y"]), crs=2193)
    sub_of = gpd.sjoin_nearest(onode, upt, how="left").groupby(level=0)["Suburb"].first().values
    sub_cand = {}
    g_idx = np.where(gain > 1e-9)[0]
    by_origin = pd.Series(g_idx).groupby(si[g_idx])
    for o, rows in by_origin:
        rows = rows.values
        _, pred = dijkstra(G, indices=S[o], return_predecessors=True)
        arc, V, depth = tree_paths(pred, S[o], attrs, keys, key_arc, n)
        tl = V[:, 0]
        dem_full = np.zeros((n, 1))
        dem_share = np.zeros((n, 1))
        tnode = Pp["t"][rows]
        np.add.at(dem_full[:, 0], tnode, gain[rows])
        np.add.at(dem_share[:, 0], tnode, gain[rows] / np.maximum(tl[tnode], 1.0))
        acc = M.accumulate(pred, depth, np.hstack([dem_full, dem_share]))
        has = arc >= 0
        ca = arc_cand[arc[has]]
        m_ = ca >= 0
        if not m_.any():
            continue
        node_ids = np.where(has)[0][m_]
        k = ca[m_]
        # full credit counts a candidate once per trip: use the flow into its first arc on each
        # route; approximate by the max arc flow per candidate (routes along a corridor share it).
        f = pd.Series(acc[node_ids, 0]).groupby(k).max()
        relied[f.index.values] += f.values
        np.add.at(shared, k, acc[node_ids, 1] * cand_len[arc[has]][m_])
        sb = sub_of[o]
        d_ = sub_cand.setdefault(sb, {})
        for kk, vv in f.items():
            d_[kk] = d_.get(kk, 0) + vv
    print(f"traced gaining trips from {len(by_origin):,} origins ({time.time() - t0:.0f}s)", flush=True)
    cand["relied_on_by"] = relied
    cand["shared_credit"] = shared
    cand["credit_per_M"] = shared / cand["cost"]
    cand["network_rank"] = cand["shared_credit"].rank(ascending=False, method="min").astype(int)

    # Suburb packages.
    sub_gain = pd.Series(gain).groupby(sub_of[si]).sum()
    pk = []
    for sb, d_ in sub_cand.items():
        if sub_gain.get(sb, 0) < MIN_SUBURB_GAIN:
            continue
        ks = [k for k, v in d_.items() if v >= PKG_SHARE * sub_gain[sb]]
        if ks:
            pk.append(dict(suburb=sb, cands=sorted(ks), suburb_gain_full=sub_gain[sb]))

    def evaluate(ks, base_l=None, base_c=None):
        il = np.unique(np.concatenate([cand.at[k, "idx_l"] for k in ks])).astype(int)
        ic = np.unique(np.concatenate([cand.at[k, "idx_c"] for k in ks])).astype(int)
        conn = {}
        for lv, c in C.items():
            new, _, _ = c.with_arcs(il, ic)
            conn[lv] = new <= cap * c.d_full
        r = riders(w_gd, w_cen, conn["confident"], conn["all_ages"])
        return r, conn

    rows = []
    for p_ in pk:
        r, _ = evaluate(p_["cands"])
        g_ = r - r_now
        cost = cand.loc[p_["cands"], "cost"].sum()
        rows.append(dict(package=f"{p_['suburb']} routes", suburb=p_["suburb"],
                         projects="; ".join(cand.loc[p_["cands"], "name"].astype(str)),
                         n_projects=len(p_["cands"]), cost=cost,
                         cost_low=cand.loc[p_["cands"], "cost_low"].sum(),
                         cost_high=cand.loc[p_["cands"], "cost_high"].sum(),
                         extra_riders=g_.sum(), extra_riders_suburb=g_[sub_of[si] == p_["suburb"]].sum(),
                         riders_per_M=g_.sum() / cost, cands=p_["cands"]))
    pkg = pd.DataFrame(rows).sort_values("riders_per_M", ascending=False).reset_index(drop=True)
    print(f"packages: {len(pkg)} ({time.time() - t0:.0f}s)", flush=True)

    # Greedy over packages (re-evaluated exactly after each is built).
    built, order, r_cur = [], [], r_now.copy()
    remaining = list(pkg.index)
    for step in range(min(N_GREEDY, len(remaining))):
        best, best_s, best_r = None, -1, None
        for i in remaining[:25]:
            ks = sorted(set(built) | set(pkg.at[i, "cands"]))
            new_ks = [k for k in pkg.at[i, "cands"] if k not in built]
            if not new_ks:
                continue
            r, _ = evaluate(ks)
            cost = cand.loc[new_ks, "cost"].sum()
            s_ = (r.sum() - r_cur.sum()) / max(cost, 0.01)
            if s_ > best_s:
                best, best_s, best_r = i, s_, r
        if best is None:
            break
        new_ks = [k for k in pkg.at[best, "cands"] if k not in built]
        built += new_ks
        remaining.remove(best)
        cost = cand.loc[built, "cost"].sum()
        order.append(dict(step=step + 1, package=pkg.at[best, "package"], added=len(new_ks),
                          step_cost=cand.loc[new_ks, "cost"].sum(), cum_cost=cost,
                          extra_riders=best_r.sum() - r_cur.sum(), riders=best_r.sum(), per_M=best_s))
        r_cur = best_r
        print(f"  step {step + 1}: {pkg.at[best, 'package']} +{order[-1]['extra_riders']:,.0f} riders, "
              f"${cost:.1f}M cumulative ({time.time() - t0:.0f}s)", flush=True)
    pkg["build_step"] = pkg["package"].map({o["package"]: o["step"] for o in order})

    # Outputs.
    ccols = ["name", "where", "type", "source", "treatment", "length_m", "cost_low", "cost", "cost_high", "plan",
             "grid_ends", "dT_godutch", "relied_on_by", "shared_credit", "credit_per_M", "network_rank"]
    cand.sort_values("shared_credit", ascending=False)[ccols].round(2).to_csv(
        P.TABLES / "cycle_plan_candidates.csv", index=False)
    pkg.drop(columns="cands").round(2).to_csv(P.TABLES / "cycle_plan_packages.csv", index=False)
    geo = gpd.GeoDataFrame(pkg.drop(columns="cands"),
                           geometry=[cand.loc[ks].geometry.union_all() for ks in pkg["cands"]], crs=2193)
    geo["geometry"] = geo.geometry.simplify(3)
    geo.to_crs(4326).to_file(P.BLOCKS / "cycle_plan_packages.geojson", driver="GeoJSON")
    summary = dict(census=float(w_cen.sum()), riders_today_network=float(r_now.sum()),
                   riders_complete_network=float(r_full.sum()), godutch=float(w_gd.sum()),
                   connected_all_ages_now=float((now["all_ages"] * w_gd).sum() / w_gd.sum() * 100),
                   connected_all_ages_complete=float((after["all_ages"] * w_gd).sum() / w_gd.sum() * 100),
                   complete_cost=float(cand["cost"].sum()), types=TYPES, greedy=order)
    (P.TABLES / "cycle_plan_summary.json").write_text(json.dumps(summary, indent=1, default=float))
    pd.set_option("display.width", 250)
    print(cand.sort_values("shared_credit", ascending=False).head(25)[
        ["name", "where", "treatment", "cost", "dT_godutch", "relied_on_by", "shared_credit", "credit_per_M"]]
        .round(1).to_string())
    print(pkg.head(20)[["package", "projects", "cost", "extra_riders", "riders_per_M", "build_step"]].round(1)
          .to_string())
    print(json.dumps({k: v for k, v in summary.items() if k != "greedy"}, default=float))


if __name__ == "__main__":
    main()
