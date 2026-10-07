"""Phase 3 (v0.3): Supply Chain Loss evaluator.

Implements SC_Loss(t) = sum over eligible (Factory_i, Logistics_j) pairs of
Loss_ij(t), where Loss_ij(t) = w_i * v_j whenever i cannot reach j within an
allowed distance using only currently-open HighwaySegments.

Eligibility and the distance threshold follow
docs/Plan_v0.3_Disaster_Recovery_WCSP.md ("Decisions"):
* only pairs with a known metapath relationship (`priority != "None"` in
  ``factory_logistics_metapath_labels.csv``) are considered;
* pairs that are already structurally unreachable in the *intact* highway
  graph (bbox graph fragmentation unrelated to the earthquake) are excluded
  entirely, rather than counted as a permanent "loss";
* the allowed distance is `max(chain_distance_threshold_km, base_km * slack)`
  -- the pair's own intact shortest-path distance with headroom, not the
  fixed 40 km label threshold alone (many real pairs legitimately exceed
  40 km but are still physically connected).
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from scipy.spatial import cKDTree

from ..highway_segments import build_segments_and_corridors, load_highway_graph
from ..regions import RegionConfig
from . import config
from .weights import compute_cluster_weights


@dataclass
class _PairInfo:
    factory_cluster_id: str
    logistics_cluster_id: str
    factory_node: object
    logistics_node: object
    w_i: float
    v_j: float
    base_km: float
    allowed_km: float


class SCLossEvaluator:
    """Precomputes eligible Factory<->Logistics pairs and their snapped
    graph nodes once, then evaluates SC_Loss(t) for a set of still-blocked
    (landslide-closed) segment ids, memoized."""

    def __init__(self, region: RegionConfig):
        self.region = region
        segments, _corridor_geoms, self.graph_u = build_segments_and_corridors(region)
        self._segment_edge: dict[str, tuple] = {
            row.segment_id: (row.u, row.v) for row in segments.itertuples()
        }

        graph_directed = load_highway_graph(region)
        node_gdf = ox.graph_to_gdfs(graph_directed, edges=False).to_crs(region.crs_metric)
        node_ids = node_gdf.index.tolist()
        coords = np.column_stack([node_gdf.geometry.x.values, node_gdf.geometry.y.values])
        kdtree = cKDTree(coords)

        clusters = gpd.read_file(region.data_processed_dir / "clusters.geojson").to_crs(region.crs_metric)
        name_to_point = {row["name"]: row.geometry for _, row in clusters.iterrows()}
        name_to_cluster_id = {row["name"]: row["cluster_id"] for _, row in clusters.iterrows()}
        weight_by_name = compute_cluster_weights(region).set_index("name")["weight_km2"].to_dict()

        pairs = pd.read_csv(region.labels_dir / "factory_logistics_metapath_labels.csv")
        pairs = pairs[pairs["priority"] != "None"]

        self.pairs: list[_PairInfo] = []
        self.n_excluded_unreachable = 0
        for row in pairs.itertuples():
            f_pt = name_to_point.get(row.factory_name)
            l_pt = name_to_point.get(row.logistics_name)
            if f_pt is None or l_pt is None:
                continue
            _, f_idx = kdtree.query([f_pt.x, f_pt.y])
            _, l_idx = kdtree.query([l_pt.x, l_pt.y])
            f_node, l_node = node_ids[f_idx], node_ids[l_idx]
            if not nx.has_path(self.graph_u, f_node, l_node):
                # pre-existing bbox graph fragmentation, unrelated to the
                # earthquake -- out of scope for SC_Loss (Decisions).
                self.n_excluded_unreachable += 1
                continue
            base_km = nx.shortest_path_length(self.graph_u, f_node, l_node, weight="length") / 1000.0
            allowed_km = max(region.chain_distance_threshold_km, base_km * config.LOSS_DISTANCE_SLACK_FACTOR)
            w_i = weight_by_name.get(row.factory_name, config.FALLBACK_MEMBER_AREA_KM2)
            v_j = weight_by_name.get(row.logistics_name, config.FALLBACK_MEMBER_AREA_KM2)
            self.pairs.append(
                _PairInfo(
                    factory_cluster_id=name_to_cluster_id.get(row.factory_name),
                    logistics_cluster_id=name_to_cluster_id.get(row.logistics_name),
                    factory_node=f_node,
                    logistics_node=l_node,
                    w_i=w_i,
                    v_j=v_j,
                    base_km=base_km,
                    allowed_km=allowed_km,
                )
            )

        self._cache: dict[frozenset, float] = {}

    def compute_sc_loss(self, blocked_segment_ids) -> float:
        """SC_Loss for the subgraph with `blocked_segment_ids` removed.

        Mutates `self.graph_u` in place (remove -> evaluate -> restore)
        rather than copying the graph, since this is called a very large
        number of times during the tabu search."""
        key = frozenset(blocked_segment_ids)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        removed = []
        for sid in key:
            edge = self._segment_edge.get(sid)
            if edge and self.graph_u.has_edge(*edge):
                data = self.graph_u.get_edge_data(*edge)
                self.graph_u.remove_edge(*edge)
                removed.append((edge, data))
        try:
            total = 0.0
            for p in self.pairs:
                if not nx.has_path(self.graph_u, p.factory_node, p.logistics_node):
                    total += p.w_i * p.v_j
                    continue
                dist_km = nx.shortest_path_length(
                    self.graph_u, p.factory_node, p.logistics_node, weight="length"
                ) / 1000.0
                if dist_km > p.allowed_km:
                    total += p.w_i * p.v_j
        finally:
            for edge, data in removed:
                self.graph_u.add_edge(edge[0], edge[1], **data)

        self._cache[key] = total
        return total
