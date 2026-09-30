"""School routes: can children ride to every Wellington City school on a low-stress network?

For each open school inside the city boundary:
1. The school is snapped to the nearest node of the main street network (largest strongly
   connected component of the routable graph).
2. Its catchment is the homes (property-level residents, pooled in 20 m cells, snapped to nodes)
   within 2 km of network distance (primary schools: Contributing, Full Primary) or 3 km
   (Intermediate, Secondary, Composite and everything else), measured home -> school on the whole
   network. Homes are weighted by residents.
3. Each home's least-effort route to the school is found on the whole network and on the low-stress
   subnetworks "all ages" (stress 1 links, crossings <= 2) and "confident" (stress <= 2), using the
   same definitions as cycle_priorities.py. Effort = metres + 30 x metres climbed. A home is
   connected when its low-stress effort is at most 1.25 x its whole-network effort. The first arc
   out of a home and the last arc into the school (up to 150 m) may be any stress. One Dijkstra
   per school on the reversed graph gives every home's cost to the school.
4. Blockers: for homes not connected at the all-ages standard, follow the least-effort route on
   the whole network and count, per street name, the residents whose route uses a link that is
   stress >= 2 in the direction of travel or ends at a crossing of stress >= 3 (residents are
   counted once per street however many blocking links of it they cross). The top three streets
   are reported. The access legs at either end are not counted as blockers.

Writes outputs/tables/cycle_schools.csv (sorted by residents not connected at all ages, largest
first) and outputs/blocks/cycle_schools.geojson (WGS84, school points with the same properties).
Caveats: a school is one point (the MoE coordinate), not its gates; catchments are distance
bands, not enrolment zones; residents are all ages, not school-age children.
"""

import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

import cycle_model as M
import cycle_priorities as P

CAP = 1.25
PRIMARY = {"Contributing", "Full Primary"}
SKIP_TYPES = {"Activity Centre", "Teen Parent Unit", "Specialist School", "Correspondence School"}


def main():
    t0 = time.time()
    e = gpd.read_parquet(M.PROC / "cycle_edges.parquet")
    nodes = pd.read_parquet(M.PROC / "cycle_nodes.parquet")
    n = len(nodes)
    a = P.arc_table(e)
    a = a.sort_values("eff").drop_duplicates(["u", "v"]).reset_index(drop=True)
    name = e["name"].fillna("").astype(str).values
    hwy = e["highway"].fillna("").astype(str).values
    street = np.array([name[i] if name[i] else f"(unnamed {hwy[i]})" for i in a["edge"].values])
    Aidx = csr_matrix((np.arange(len(a)) + 1, (a["u"].values, a["v"].values)), shape=(n, n))
    _, lab = connected_components(csr_matrix((np.ones(len(a)), (a["u"], a["v"])), shape=(n, n)),
                                  directed=True, connection="strong")
    keep = lab == np.bincount(lab).argmax()

    # Schools
    sa2 = gpd.read_file(M.RAW / "sa2.geojson").to_crs(2193)
    city = sa2.union_all()
    sch = gpd.read_file(M.CYC / "schools.geojson").set_crs(2193, allow_override=True)
    sch = sch[(sch["Status"] == "Open") & ~sch["Org_Type"].isin(SKIP_TYPES) & sch.within(city)].reset_index(drop=True)
    sch["node"], sch["snap_m"] = M.snap(np.c_[sch.geometry.x, sch.geometry.y], nodes, keep)
    sch["catch_m"] = np.where(sch["Org_Type"].isin(PRIMARY), 2000, 3000)
    # Homes
    pts = M.zones()[0]
    pts = pts[(pts["residents"] > 0) & pts.within(city)]
    hn, _ = M.snap(np.c_[pts.geometry.x, pts.geometry.y], nodes, keep)
    res = pd.Series(pts["residents"].values).groupby(hn).sum()
    hnode, hres = res.index.values, res.values
    print(f"{len(sch)} schools, {len(hnode):,} home nodes, {hres.sum():,.0f} residents")

    # Graphs (reversed = transposed: distance from the school to every node, i.e. node -> school)
    isacc_home = np.zeros(n, bool)
    isacc_home[hnode] = True
    access = ((isacc_home[a["u"].values] | np.isin(a["v"].values, sch["node"].values))
              & (a["len"].values <= P.ACCESS_M) & (a["lts"].values < P.BARRED))
    R = {}
    R["len"] = P.csr(a, np.ones(len(a), bool), n, "len").T.tocsr()
    R["full"] = P.csr(a, np.ones(len(a), bool), n, "eff").T.tocsr()
    for lv in ("all_ages", "confident"):
        R[lv] = P.csr(a, P.low_stress(a, lv, access=access), n, "eff").T.tocsr()
    blocking = ((a["lts"].values >= 2) | (a["cross"].values >= 3))
    bname = np.where(blocking, street, "")
    ulen = a["len"].values

    rows = []
    for k, s in sch.iterrows():
        src = int(s["node"])
        dl = dijkstra(R["len"], indices=src, limit=s["catch_m"])
        inside = np.isfinite(dl[hnode]) & (dl[hnode] <= s["catch_m"]) & (hnode != src)
        hs, w = hnode[inside], hres[inside]
        out = dict(school=s["Org_Name"], type=s["Org_Type"], roll=s["Total"], catchment_km=s["catch_m"] / 1000,
                   residents=round(w.sum()), snap_m=round(s["snap_m"]))
        if len(hs) == 0:
            rows.append({**out, "pct_all_ages": np.nan, "pct_confident": np.nan, "unconnected": 0, "unconnected_conf": 0,
                         "geometry": s.geometry})
            continue
        df, pred = dijkstra(R["full"], indices=src, return_predecessors=True)
        d_aa = dijkstra(R["all_ages"], indices=src)
        d_cf = dijkstra(R["confident"], indices=src)
        base = df[hs]
        c_aa = d_aa[hs] <= CAP * base
        c_cf = d_cf[hs] <= CAP * base
        out.update(pct_all_ages=100 * w[c_aa].sum() / w.sum(), pct_confident=100 * w[c_cf].sum() / w.sum(),
                   unconnected=w[~c_aa].sum(), unconnected_conf=w[~c_cf].sum())
        # Blockers: walk each unconnected home's route to the school.
        cur = hs[~c_aa]
        wt = w[~c_aa]
        first = np.ones(len(cur), bool)
        home_id = np.arange(len(cur))
        recs = []
        live = np.ones(len(cur), bool)
        while live.any():
            x = cur[live]
            nxt = pred[x]
            ok = nxt >= 0
            ai = np.where(ok, np.asarray(Aidx[x, np.where(ok, nxt, 0)]).ravel() - 1, -1)
            ai = np.asarray(ai)
            ids = home_id[live]
            access_leg = (ulen[np.maximum(ai, 0)] <= P.ACCESS_M) & (first[live] | (nxt == src))
            hit = ok & (ai >= 0) & ~access_leg
            nm = np.where(hit, bname[np.maximum(ai, 0)], "")
            sel = nm != ""
            if sel.any():
                recs.append(pd.DataFrame({"h": ids[sel], "street": nm[sel]}))
            idx = np.where(live)[0]
            first[idx] = False
            cur[idx] = np.where(ok, nxt, x)
            live[idx[~ok | (nxt == src)]] = False
        if recs:
            r = pd.concat(recs).drop_duplicates()
            r["w"] = wt[r["h"].values]
            top = r.groupby("street")["w"].sum().sort_values(ascending=False).head(3)
        else:
            top = pd.Series(dtype=float)
        for j in range(3):
            out[f"blocker_{j + 1}"] = top.index[j] if j < len(top) else ""
            out[f"blocker_{j + 1}_residents"] = round(top.iloc[j]) if j < len(top) else 0
        out["geometry"] = s.geometry
        rows.append(out)
        if (k + 1) % 20 == 0:
            print(f"  {k + 1}/{len(sch)} schools, {time.time() - t0:.0f}s")

    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=2193)
    # Suburb: nearest rating unit's suburb
    units = gpd.read_parquet(M.PROC / "units.parquet")[["Suburb", "geometry"]].dropna(subset=["Suburb"])
    j = gpd.sjoin_nearest(g[["geometry"]], units, how="left").reset_index().drop_duplicates("index").set_index("index")
    g["suburb"] = j["Suburb"].reindex(g.index).fillna("").values
    for c in ("blocker_1", "blocker_2", "blocker_3"):
        g[c] = g[c].fillna("")
    g["unconnected"] = g["unconnected"].fillna(0).round()
    g["unconnected_conf"] = g["unconnected_conf"].fillna(0).round()
    g[["pct_all_ages", "pct_confident"]] = g[["pct_all_ages", "pct_confident"]].round(1)
    g = g.sort_values("unconnected", ascending=False).reset_index(drop=True)
    cols = ["school", "type", "roll", "suburb", "catchment_km", "residents", "pct_all_ages", "pct_confident",
            "unconnected", "blocker_1", "blocker_2", "blocker_3", "blocker_1_residents", "blocker_2_residents",
            "blocker_3_residents", "snap_m"]
    M.TABLES.mkdir(parents=True, exist_ok=True)
    g[cols].to_csv(M.TABLES / "cycle_schools.csv", index=False)
    (M.ROOT / "outputs" / "blocks").mkdir(parents=True, exist_ok=True)
    g[cols + ["unconnected_conf", "geometry"]].to_crs(4326).to_file(M.ROOT / "outputs" / "blocks" / "cycle_schools.geojson",
                                                                    driver="GeoJSON")
    tot = g["residents"].sum()
    print(f"\nCatchment residents (schools double count nearby homes): {tot:,.0f}")
    print(f"Connected at all ages: {100 * (1 - g['unconnected'].sum() / tot):.1f}%   "
          f"confident: {100 * (1 - g['unconnected_conf'].sum() / tot):.1f}%")
    with pd.option_context("display.width", 250, "display.max_columns", 20, "display.max_colwidth", 30):
        print(g[["school", "type", "residents", "pct_all_ages", "pct_confident", "unconnected", "blocker_1", "blocker_2",
                 "blocker_3"]].head(15).to_string())
    print(f"snap > 100 m: ", g.loc[g["snap_m"] > 100, ["school", "snap_m"]].values.tolist())
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
