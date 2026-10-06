"""Step 2/3 (metapath labeling): NEAR_HIGHWAY edges + the two Highway-neighbor
metapath label types defined in the MVP concept:

* H_NEIGHBOR(r)          : FactoryCluster -NEAR_HIGHWAY-> corridor
                           <-NEAR_HIGHWAY- LogisticsCluster, same corridor,
                           both within radius r (r in NEIGHBOR_RADII_KM).
* H_CHAIN_ACCESS(distance): shortest path length along the
                           HIGHWAY_CONTIGUOUS road-network graph between the
                           nearest highway node to each cluster, labeled when
                           <= CHAIN_DISTANCE_THRESHOLD_KM.
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
from scipy.spatial import cKDTree

from . import config, regions
from .highway_segments import build_segments_and_corridors, load_highway_graph
from .regions import RegionConfig


def _load_clusters(region: RegionConfig) -> gpd.GeoDataFrame:
    clusters = gpd.read_file(region.data_processed_dir / "clusters.geojson")
    return clusters.to_crs(region.crs_metric)


def compute_near_highway(clusters_metric: gpd.GeoDataFrame, corridor_geoms: dict, region: RegionConfig) -> list[dict]:
    """NEAR_HIGHWAY(cluster, corridor) rows for every pair within max(region.neighbor_radii_km)."""
    max_radius_km = max(region.neighbor_radii_km)
    rows = []
    for _, cl in clusters_metric.iterrows():
        pt = cl.geometry
        for corridor_id, geom in corridor_geoms.items():
            dist_km = pt.distance(geom) / 1000.0
            if dist_km <= max_radius_km:
                rows.append(
                    {
                        "cluster_id": cl["cluster_id"],
                        "name": cl["name"],
                        "node_type": cl["node_type"],
                        "corridor_id": corridor_id,
                        "distance_km": round(dist_km, 3),
                    }
                )
    return rows


def _radius_tier(distance_km: float, region: RegionConfig) -> float | None:
    for r in sorted(region.neighbor_radii_km):
        if distance_km <= r:
            return r
    return None


def compute_h_neighbor(near_highway_rows: list[dict], region: RegionConfig) -> list[dict]:
    """Factory-Highway-Logistics metapath: pairs sharing a corridor, labeled
    with the smallest radius tier at which both endpoints are within range."""
    import pandas as pd

    nh = pd.DataFrame(near_highway_rows)
    if nh.empty:
        return []
    factories = nh[nh["node_type"] == "factory"]
    logistics = nh[nh["node_type"] == "logistics"]

    results = []
    merged = factories.merge(logistics, on="corridor_id", suffixes=("_factory", "_logistics"))
    for _, row in merged.iterrows():
        r_pair = max(row["distance_km_factory"], row["distance_km_logistics"])
        tier = _radius_tier(r_pair, region)
        if tier is None:
            continue
        results.append(
            {
                "factory_cluster_id": row["cluster_id_factory"],
                "factory_name": row["name_factory"],
                "logistics_cluster_id": row["cluster_id_logistics"],
                "logistics_name": row["name_logistics"],
                "corridor_id": row["corridor_id"],
                "factory_distance_km": row["distance_km_factory"],
                "logistics_distance_km": row["distance_km_logistics"],
                "r_pair_km": round(r_pair, 3),
                "radius_tier_km": tier,
                "label": f"H_NEIGHBOR(r={tier:g}km)",
            }
        )
    return results


def _nearest_graph_node(kdtree: cKDTree, node_ids: list, point) -> tuple:
    dist, idx = kdtree.query([point.x, point.y])
    return node_ids[idx], dist


def compute_h_chain_access(
    clusters_metric: gpd.GeoDataFrame,
    graph_u: nx.Graph,
    graph_directed: nx.MultiDiGraph,
    region: RegionConfig,
) -> list[dict]:
    """Factory-HighwayChain-Logistics metapath: shortest HIGHWAY_CONTIGUOUS
    road-network path between the nearest highway node to each cluster."""
    node_gdf = ox.graph_to_gdfs(graph_directed, edges=False).to_crs(region.crs_metric)
    node_ids = node_gdf.index.tolist()
    coords = np.column_stack([node_gdf.geometry.x.values, node_gdf.geometry.y.values])
    kdtree = cKDTree(coords)

    factories = clusters_metric[clusters_metric["node_type"] == "factory"]
    logistics = clusters_metric[clusters_metric["node_type"] == "logistics"]

    results = []
    for _, f in factories.iterrows():
        f_node, f_snap_dist = _nearest_graph_node(kdtree, node_ids, f.geometry)
        for _, l in logistics.iterrows():
            l_node, l_snap_dist = _nearest_graph_node(kdtree, node_ids, l.geometry)
            try:
                path_nodes = nx.shortest_path(graph_u, f_node, l_node, weight="length")
                path_length_m = nx.shortest_path_length(graph_u, f_node, l_node, weight="length")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            distance_km = (path_length_m + f_snap_dist + l_snap_dist) / 1000.0
            path_corridors = _path_corridors(graph_directed, path_nodes)
            label = None
            if distance_km <= region.chain_distance_threshold_km:
                label = f"H_CHAIN_ACCESS(distance={distance_km:.1f}km)"
            results.append(
                {
                    "factory_cluster_id": f["cluster_id"],
                    "factory_name": f["name"],
                    "logistics_cluster_id": l["cluster_id"],
                    "logistics_name": l["name"],
                    "road_distance_km": round(distance_km, 2),
                    "within_threshold": distance_km <= region.chain_distance_threshold_km,
                    "label": label,
                    "path_node_ids": path_nodes,
                    "path_corridors": path_corridors,
                }
            )
    return results


def _path_corridors(graph_directed: nx.MultiDiGraph, path_nodes: list) -> list[str]:
    """Best-effort list of corridor ids (ref/name) traversed by a node path,
    by looking up the edge between each consecutive node pair."""
    from .highway_segments import _normalize_tag

    corridors = []
    for u, v in zip(path_nodes[:-1], path_nodes[1:]):
        data = None
        if graph_directed.has_edge(u, v):
            data = graph_directed.get_edge_data(u, v)[0]
        elif graph_directed.has_edge(v, u):
            data = graph_directed.get_edge_data(v, u)[0]
        if data is None:
            continue
        ref = _normalize_tag(data.get("ref"))
        name = _normalize_tag(data.get("name"))
        corridors.append(ref or name or "unnamed")
    # de-duplicate while preserving order
    seen = []
    for c in corridors:
        if not seen or seen[-1] != c:
            seen.append(c)
    return seen


def run_all(region: RegionConfig):
    clusters_metric = _load_clusters(region)
    segments, corridor_geoms, graph_u = build_segments_and_corridors(region)
    graph_directed = load_highway_graph(region)

    near_highway_rows = compute_near_highway(clusters_metric, corridor_geoms, region)
    h_neighbor_rows = compute_h_neighbor(near_highway_rows, region)
    h_chain_rows = compute_h_chain_access(clusters_metric, graph_u, graph_directed, region)

    return {
        "near_highway": near_highway_rows,
        "h_neighbor": h_neighbor_rows,
        "h_chain": h_chain_rows,
        "segments": segments,
    }


if __name__ == "__main__":
    _region = regions.get_region("gurgaon")
    out = run_all(_region)
    print(f"NEAR_HIGHWAY edges: {len(out['near_highway'])}")
    print(f"H_NEIGHBOR pairs: {len(out['h_neighbor'])}")
    print(f"H_CHAIN_ACCESS pairs (incl. over-threshold): {len(out['h_chain'])}")
    n_within = sum(1 for r in out["h_chain"] if r["within_threshold"])
    print(f"H_CHAIN_ACCESS pairs within {_region.chain_distance_threshold_km}km: {n_within}")
