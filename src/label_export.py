"""Step 3 (export): collapse the raw NEAR_HIGHWAY / H_NEIGHBOR / H_CHAIN_ACCESS
rows from metapath.py into the two MVP deliverables:

1. Factory x Logistics pair label table (outputs/labels/pair_labels.csv)
   -> "IMT Manesar <-> Luhari Logistics Estate is on NH48, ~Xkm, H_NEIGHBOR(r=..)"
2. HighwaySegment hard-constraint flag table (outputs/labels/segment_hard_constraint_flags.csv)
   -> feeds the metapath label into method_repair_lot_wcsp's Hard_j decision
      (Definition 8): segments on a qualifying metapath are flagged so a
      repair lot overlapping them is forced into the repair plan regardless
      of its crack/pothole benefit-cost score.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from . import config, metapath


def _priority(radius_tier_km: float | None, chain_distance_km: float | None) -> str:
    high = (radius_tier_km is not None and radius_tier_km <= config.PRIORITY_HIGH_NEIGHBOR_KM) or (
        chain_distance_km is not None and chain_distance_km <= config.PRIORITY_HIGH_CHAIN_KM
    )
    medium = (radius_tier_km is not None) or (
        chain_distance_km is not None and chain_distance_km <= config.CHAIN_DISTANCE_THRESHOLD_KM
    )
    if high:
        return "High"
    if medium:
        return "Medium"
    return "None"


def build_pair_label_table(results: dict) -> pd.DataFrame:
    h_neighbor = pd.DataFrame(results["h_neighbor"])
    h_chain = pd.DataFrame(results["h_chain"])
    named_names = set(metapath._load_clusters().loc[lambda d: d["is_named_seed"], "name"])

    # best (smallest-radius) H_NEIGHBOR row per (factory, logistics) pair
    if not h_neighbor.empty:
        h_neighbor_best = (
            h_neighbor.sort_values("r_pair_km")
            .groupby(["factory_cluster_id", "logistics_cluster_id"], as_index=False)
            .first()
        )
    else:
        h_neighbor_best = pd.DataFrame(
            columns=[
                "factory_cluster_id", "factory_name", "logistics_cluster_id", "logistics_name",
                "corridor_id", "factory_distance_km", "logistics_distance_km",
                "r_pair_km", "radius_tier_km", "label",
            ]
        )

    pairs = pd.merge(
        h_chain,
        h_neighbor_best,
        on=["factory_cluster_id", "factory_name", "logistics_cluster_id", "logistics_name"],
        how="outer",
        suffixes=("_chain", "_neighbor"),
    )

    out_rows = []
    for _, row in pairs.iterrows():
        radius_tier_km = row.get("radius_tier_km")
        chain_distance_km = row.get("road_distance_km")
        within_threshold = bool(row.get("within_threshold")) if pd.notna(row.get("within_threshold")) else False
        priority = _priority(
            radius_tier_km if pd.notna(radius_tier_km) else None,
            chain_distance_km if (pd.notna(chain_distance_km) and within_threshold) else None,
        )
        labels = [l for l in (row.get("label_neighbor"), row.get("label_chain")) if isinstance(l, str)]
        out_rows.append(
            {
                "factory_name": row["factory_name"],
                "logistics_name": row["logistics_name"],
                "is_named_pair": row["factory_name"] in named_names and row["logistics_name"] in named_names,
                "shared_corridor": row.get("corridor_id"),
                "h_neighbor_radius_tier_km": radius_tier_km,
                "h_neighbor_label": row.get("label_neighbor"),
                "road_distance_km": chain_distance_km,
                "h_chain_access_label": row.get("label_chain") if within_threshold else None,
                "metapath_labels": "; ".join(labels) if labels else "",
                "priority": priority,
                "path_corridors": "; ".join(row.get("path_corridors") or []) if isinstance(row.get("path_corridors"), list) else "",
            }
        )

    df = pd.DataFrame(out_rows)
    if df.empty:
        return df
    priority_order = {"High": 0, "Medium": 1, "None": 2}
    df["_order"] = df["priority"].map(priority_order)
    df = df.sort_values(["_order", "road_distance_km"], na_position="last").drop(columns="_order")
    return df.reset_index(drop=True)


def build_segment_hard_constraint_table(results: dict, pair_table: pd.DataFrame) -> gpd.GeoDataFrame:
    """Flag each HighwaySegment edge precisely, instead of flagging an entire
    named corridor.

    Only *named* Factory<->Logistics pairs (the 6 concept seed places, e.g.
    IMT Manesar <-> Bilaspur Logistics Hub) drive the hard-constraint flag;
    the many small DBSCAN "auto" clusters (single/double OSM polygons, not a
    recognised Industrial Park / Logistics District) are kept in the full
    pair-label CSV for transparency but are excluded here so the exported
    hard-constraint flag stays precise and does not over-trigger
    method_repair_lot_wcsp's Definition 8 for unrelated stretches of road.

    * H_NEIGHBOR-driven flag: the edge belongs to the pair's shared corridor
      AND lies within the matched radius tier of the factory or logistics
      cluster centroid (i.e. the actual near-highway stretch).
    * H_CHAIN_ACCESS-driven flag: the edge is literally one of the edges on
      that pair's shortest HIGHWAY_CONTIGUOUS path (not merely "same corridor
      name" somewhere else in the network).
    """
    segments = results["segments"].copy()
    segments_metric = segments.to_crs(config.CRS_METRIC)
    clusters_metric = metapath._load_clusters()
    name_to_point = {row["name"]: row.geometry for _, row in clusters_metric.iterrows()}
    name_to_id = {row["name"]: row["cluster_id"] for _, row in clusters_metric.iterrows()}

    # adjacency (u, v) -> [segment_id, ...], both directions, for path-edge lookup
    adjacency: dict[tuple, list[str]] = {}
    for _, row in segments.iterrows():
        adjacency.setdefault((row["u"], row["v"]), []).append(row["segment_id"])
        adjacency.setdefault((row["v"], row["u"]), []).append(row["segment_id"])

    chain_lookup = {
        (r["factory_cluster_id"], r["logistics_cluster_id"]): r for r in results["h_chain"]
    }

    priority_rank = {"High": 2, "Medium": 1, "None": 0}
    seg_priority: dict[str, str] = {sid: "None" for sid in segments["segment_id"]}
    seg_pairs: dict[str, set] = {sid: set() for sid in segments["segment_id"]}

    def _bump(sid, priority, pair_label):
        if priority_rank[priority] > priority_rank[seg_priority[sid]]:
            seg_priority[sid] = priority
        seg_pairs[sid].add(pair_label)

    active = pair_table[
        pair_table["priority"].isin(["High", "Medium"]) & pair_table["is_named_pair"]
    ]
    for _, row in active.iterrows():
        pair_label = f"{row['factory_name']} <-> {row['logistics_name']}"
        priority = row["priority"]

        # --- H_NEIGHBOR-driven: near-highway stretch of the shared corridor ---
        corridor_id = row.get("shared_corridor")
        radius_tier_km = row.get("h_neighbor_radius_tier_km")
        if corridor_id and pd.notna(corridor_id) and pd.notna(radius_tier_km):
            f_pt = name_to_point.get(row["factory_name"])
            l_pt = name_to_point.get(row["logistics_name"])
            radius_m = radius_tier_km * 1000.0
            subset = segments_metric[segments_metric["corridor_id"] == corridor_id]
            for sid, geom in zip(subset["segment_id"], subset.geometry):
                near = False
                if f_pt is not None and geom.distance(f_pt) <= radius_m:
                    near = True
                if l_pt is not None and geom.distance(l_pt) <= radius_m:
                    near = True
                if near:
                    _bump(sid, priority, pair_label)

        # --- H_CHAIN_ACCESS-driven: exact shortest-path edges ---
        # Capped at "Medium": the full connecting route is informative
        # context (the pair is linked via this corridor), but only the
        # near-highway stretch actually adjacent to a named cluster
        # (H_NEIGHBOR, above) should escalate to "High" / is_metapath_hard,
        # otherwise the entire multi-ten-km route would be marked hard.
        key = (name_to_id.get(row["factory_name"]), name_to_id.get(row["logistics_name"]))
        chain = chain_lookup.get(key)
        if chain is not None and chain.get("within_threshold"):
            path_nodes = chain["path_node_ids"]
            for u, v in zip(path_nodes[:-1], path_nodes[1:]):
                for sid in adjacency.get((u, v), []):
                    _bump(sid, "Medium", pair_label)

    segments["metapath_priority"] = segments["segment_id"].map(seg_priority)
    segments["is_metapath_hard"] = segments["metapath_priority"] == "High"
    segments["metapath_pairs"] = segments["segment_id"].map(lambda sid: "; ".join(sorted(seg_pairs[sid])))
    return segments[
        ["segment_id", "corridor_id", "highway", "length", "metapath_priority", "is_metapath_hard", "metapath_pairs", "geometry"]
    ]


def run_export():
    results = metapath.run_all()
    pair_table = build_pair_label_table(results)
    segment_table = build_segment_hard_constraint_table(results, pair_table)

    pair_csv = config.LABELS_DIR / "factory_logistics_metapath_labels.csv"
    pair_table.to_csv(pair_csv, index=False, encoding="utf-8-sig")

    seg_csv = config.LABELS_DIR / "segment_hard_constraint_flags.csv"
    segment_table.drop(columns="geometry").to_csv(seg_csv, index=False, encoding="utf-8-sig")
    seg_geojson = config.LABELS_DIR / "segment_hard_constraint_flags.geojson"
    segment_table.to_file(seg_geojson, driver="GeoJSON")

    return pair_table, segment_table


if __name__ == "__main__":
    pair_table, segment_table = run_export()
    print(pair_table.to_string(max_colwidth=40))
    print()
    print(f"segments flagged hard (High priority): {segment_table['is_metapath_hard'].sum()} / {len(segment_table)}")
    print(f"saved -> {config.LABELS_DIR}")
