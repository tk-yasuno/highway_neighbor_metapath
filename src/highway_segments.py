"""Step 1/2 (highway topology): load the OSM road graph as HighwaySegment
edges, group them into named "corridors" (NH48, Sohna Road, ...) via the OSM
`ref`/`name` tags, and expose a routable networkx graph for HIGHWAY_CONTIGUOUS
shortest-path queries (used by the H_CHAIN_ACCESS metapath).
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd

from . import config, regions
from .regions import RegionConfig


def _normalize_tag(value) -> str | None:
    """OSM ref/name tags are sometimes lists (multiplexed ways); take the
    first value and strip whitespace."""
    if value is None or (isinstance(value, float)):
        return None
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else None
    return str(value).strip()


def load_highway_graph(region: RegionConfig) -> nx.MultiDiGraph:
    return ox.load_graphml(region.data_raw_dir / "highway_graph.graphml")


def build_segments_and_corridors(region: RegionConfig, graph: nx.MultiDiGraph | None = None):
    """Return (segments_gdf, corridor_geoms, graph_undirected).

    segments_gdf : one row per HighwaySegment (OSM way edge) with a
        `corridor_id` column (NH48, "Sohna Road", or a per-edge fallback id
        for unnamed/un-refed ramps).
    corridor_geoms : dict[corridor_id -> shapely geometry] (union of member
        edges), in metric CRS, used for NEAR_HIGHWAY distance queries.
    graph_undirected : nx.Graph with edge weight "length" (metres), used for
        HIGHWAY_CONTIGUOUS shortest-path (H_CHAIN_ACCESS) queries.
    """
    graph = graph or load_highway_graph(region)
    edges = ox.graph_to_gdfs(graph, nodes=False).reset_index()

    corridor_ids = []
    for i, row in edges.iterrows():
        ref = _normalize_tag(row.get("ref"))
        name = _normalize_tag(row.get("name"))
        if ref:
            corridor_ids.append(ref)
        elif name:
            corridor_ids.append(name)
        else:
            corridor_ids.append(f"segment_{i}")
    edges["corridor_id"] = corridor_ids
    edges["segment_id"] = [f"S{i}" for i in range(len(edges))]

    edges_metric = edges.to_crs(region.crs_metric)
    corridor_geoms = {
        cid: grp.geometry.union_all()
        for cid, grp in edges_metric.groupby("corridor_id")
    }

    graph_u = nx.Graph()
    for u, v, data in graph.edges(data=True):
        length = data.get("length", 1.0)
        if graph_u.has_edge(u, v):
            if length < graph_u[u][v]["length"]:
                graph_u[u][v]["length"] = length
        else:
            graph_u.add_edge(u, v, length=length)
    for n, data in graph.nodes(data=True):
        graph_u.add_node(n, **data)

    # Junction / IC proxy: nodes with degree >= 3 in the simplified graph.
    for n in graph_u.nodes:
        graph_u.nodes[n]["is_junction"] = graph_u.degree(n) >= 3

    return edges[["segment_id", "corridor_id", "highway", "ref", "name", "length", "u", "v", "geometry"]], corridor_geoms, graph_u


if __name__ == "__main__":
    _region = regions.get_region("gurgaon")
    segments, corridors, g_u = build_segments_and_corridors(_region)
    out_path = _region.data_processed_dir / "highway_segments.geojson"
    segments.to_file(out_path, driver="GeoJSON")
    print(f"segments: {len(segments)}, corridors: {len(corridors)}")
    print(f"undirected graph: {g_u.number_of_nodes()} nodes, {g_u.number_of_edges()} edges")
    n_junctions = sum(1 for _, d in g_u.nodes(data=True) if d.get("is_junction"))
    print(f"junction nodes (degree>=3): {n_junctions}")
    top_corridors = segments.groupby("corridor_id")["length"].sum().sort_values(ascending=False).head(10)
    print(top_corridors)
    print(f"saved -> {out_path}")
