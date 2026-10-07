"""Step 1 (clustering): turn raw OSM industrial/warehouse geometries into the
MVP's FactoryCluster / LogisticsCluster node set via DBSCAN, then snap each
cluster to a human-readable name using the named seed places in config.py.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from sklearn.cluster import DBSCAN

from . import config, regions
from .regions import RegionConfig


def _load_points(region: RegionConfig) -> gpd.GeoDataFrame:
    """Load industrial polygons + warehouse features as a single point layer
    (projected to metric CRS), tagged with their OSM source type."""
    ind = gpd.read_file(region.data_raw_dir / "industrial_polygons.geojson")
    wh = gpd.read_file(region.data_raw_dir / "warehouse_features.geojson")

    ind = ind.to_crs(region.crs_metric)
    wh = wh.to_crs(region.crs_metric)

    ind_pts = gpd.GeoDataFrame(
        {
            "osm_element": ind["element"],
            "osm_id": ind["id"],
            "osm_name": ind["name"] if "name" in ind.columns else None,
            "src_type": "industrial",
            # real polygon footprint area (0.0 for a bare Point geometry, e.g. a
            # future building=warehouse node) -- used by v0.3's disaster-recovery
            # supply-chain weighting (w_i / v_j proportional to cluster area).
            "member_area_m2": ind.geometry.area,
        },
        geometry=ind.geometry.centroid,
        crs=region.crs_metric,
    )
    wh_pts = gpd.GeoDataFrame(
        {
            "osm_element": wh["element"],
            "osm_id": wh["id"],
            "osm_name": wh["name"] if "name" in wh.columns else None,
            "src_type": "warehouse",
            "member_area_m2": wh.geometry.area,
        },
        geometry=wh.geometry.centroid,
        crs=region.crs_metric,
    )
    pts = pd.concat([ind_pts, wh_pts], ignore_index=True)
    return gpd.GeoDataFrame(pts, geometry="geometry", crs=region.crs_metric)


def _dbscan_cluster(points: gpd.GeoDataFrame, region: RegionConfig) -> np.ndarray:
    coords = np.column_stack([points.geometry.x.values, points.geometry.y.values])
    db = DBSCAN(eps=region.dbscan_eps_m, min_samples=region.dbscan_min_samples)
    labels = db.fit_predict(coords)
    # Treat every noise point (-1) as its own singleton cluster instead of
    # discarding it: sparse Indian OSM tagging means isolated-but-real named
    # industrial/warehouse polygons are common and should not be dropped.
    next_id = labels.max() + 1 if len(labels) else 0
    for i, lab in enumerate(labels):
        if lab == -1:
            labels[i] = next_id
            next_id += 1
    return labels


def _seed_assignment(cluster_centroids: gpd.GeoSeries, region: RegionConfig) -> dict[int, tuple[str, str]]:
    """Greedily snap each named seed place to its nearest cluster (within
    region.seed_match_tolerance_km), returning {cluster_idx: (name, type)}.

    Regions with no curated ``seed_places`` (the v0.2 generality case
    studies) simply return an empty mapping, leaving every cluster as an
    auto-named ``FactoryCluster_auto_N`` / ``LogisticsCluster_auto_N``."""
    if not region.seed_places:
        return {}
    seed_pts = {
        name: gpd.GeoSeries([Point(info["lon"], info["lat"])], crs=config.CRS_WGS84)
        .to_crs(region.crs_metric)
        .iloc[0]
        for name, info in region.seed_places.items()
    }
    tolerance_m = region.seed_match_tolerance_km * 1000.0

    # distance matrix: seeds x clusters
    seed_names = list(seed_pts.keys())
    dists = np.array(
        [[seed_pts[s].distance(c) for c in cluster_centroids] for s in seed_names]
    )

    assigned: dict[int, tuple[str, str]] = {}
    used_clusters: set[int] = set()
    # greedy: repeatedly pick the globally closest (seed, cluster) pair
    flat_order = np.dstack(np.unravel_index(np.argsort(dists, axis=None), dists.shape))[0]
    used_seeds: set[int] = set()
    for seed_idx, cluster_idx in flat_order:
        seed_idx, cluster_idx = int(seed_idx), int(cluster_idx)
        if seed_idx in used_seeds or cluster_idx in used_clusters:
            continue
        if dists[seed_idx, cluster_idx] > tolerance_m:
            continue
        name = seed_names[seed_idx]
        assigned[cluster_idx] = (name, region.seed_places[name]["type"])
        used_seeds.add(seed_idx)
        used_clusters.add(cluster_idx)
    return assigned


def build_clusters(region: RegionConfig) -> gpd.GeoDataFrame:
    """Run the full clustering step and return one row per FactoryCluster /
    LogisticsCluster node, in WGS84, with columns:
    cluster_id, name, node_type ("factory"/"logistics"), n_members,
    member_names, is_named_seed, area_m2 (summed real footprint area of the
    cluster's member OSM polygons, 0.0 if all members are bare points; used
    by v0.3's disaster-recovery supply-chain weighting), geometry (centroid
    point).
    """
    points = _load_points(region)
    labels = _dbscan_cluster(points, region)
    points = points.assign(cluster_label=labels)

    rows = []
    cluster_ids = sorted(points["cluster_label"].unique())
    centroids = []
    for cid in cluster_ids:
        members = points[points["cluster_label"] == cid]
        centroid = members.geometry.union_all().centroid
        centroids.append(centroid)
    centroids_gs = gpd.GeoSeries(centroids, crs=region.crs_metric)

    seed_map = _seed_assignment(centroids_gs, region)

    for i, cid in enumerate(cluster_ids):
        members = points[points["cluster_label"] == cid]
        n_industrial = (members["src_type"] == "industrial").sum()
        n_warehouse = (members["src_type"] == "warehouse").sum()
        default_type = "factory" if n_industrial >= n_warehouse else "logistics"
        member_names = sorted({n for n in members["osm_name"].dropna().tolist()})
        area_m2 = float(members["member_area_m2"].sum())

        if i in seed_map:
            name, node_type = seed_map[i]
            is_named_seed = True
        else:
            node_type = default_type
            prefix = "FactoryCluster" if node_type == "factory" else "LogisticsCluster"
            name = f"{prefix}_auto_{cid}"
            is_named_seed = False

        rows.append(
            {
                "cluster_id": f"C{cid}",
                "name": name,
                "node_type": node_type,
                "n_members": len(members),
                "n_industrial": int(n_industrial),
                "n_warehouse": int(n_warehouse),
                "member_names": "; ".join(member_names) if member_names else "",
                "is_named_seed": is_named_seed,
                "area_m2": area_m2,
                "geometry": centroids_gs.iloc[i],
            }
        )

    clusters = gpd.GeoDataFrame(rows, geometry="geometry", crs=region.crs_metric)
    clusters = clusters.to_crs(config.CRS_WGS84)
    return clusters


if __name__ == "__main__":
    from . import regions as _regions

    _region = _regions.get_region("gurgaon")
    clusters = build_clusters(_region)
    out_path = _region.data_processed_dir / "clusters.geojson"
    clusters.to_file(out_path, driver="GeoJSON")
    print(clusters[["cluster_id", "name", "node_type", "n_members", "is_named_seed"]].to_string())
    print(f"saved -> {out_path}")
