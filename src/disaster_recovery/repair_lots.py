"""Phase 2 (v0.3): Repair Lot construction.

Bundles chainage-adjacent damaged HighwaySegments (crack/pothole and
landslide-block, i.e. ``damage_type != "none"``) into Repair Lots, the
construction unit the WCSP schedules as a whole.

Unlike the parent Repair Lot Skyline WCSP's chainage-gap clustering
(Definition 4), this repository's schema has discrete OSM way edges rather
than continuous chainage, so adjacency is taken directly from the
HIGHWAY_CONTIGUOUS node-sharing topology (each triggered segment's `(u, v)`
endpoints) instead of a gap tolerance `g`. Bundling also deliberately
ignores `corridor_id`: Gurgaon's 900 segments fall into 489 corridors (many
singleton fallback corridors for unnamed/un-refed ramps), so corridor-based
bundling would over-fragment physically contiguous damaged stretches (see
docs/Plan_v0.3_Disaster_Recovery_WCSP.md, "Decisions").

Each lot also gets a `duration_days` estimate (length / damage-type-specific
production rate, see `config.PRODUCTION_RATE_M_PER_DAY`), so the scheduler
can occupy a crew for a lot's full, realistic repair duration instead of
treating every lot as a fixed one-day job regardless of length.
"""

from __future__ import annotations

import math

import geopandas as gpd
import networkx as nx
import pandas as pd

from ..regions import RegionConfig
from . import config
from .weights import compute_cluster_weights

_LOT_COLUMNS = [
    "lot_id",
    "segment_ids",
    "n_segments",
    "length_total_m",
    "duration_days",
    "primary_corridor_id",
    "hard_disaster",
    "hard_metapath",
    "hard",
    "cost_k",
    "impact_weight_k",
]


def _lot_duration_days(members: pd.DataFrame) -> int:
    """Sum, over the lot's member segments, of length / production-rate for
    that segment's damage type, rounded up to whole crew-days (min 1 day).
    A 5.8 km landslide-blocked lot thus realistically takes ~58 days, not 1
    (see docs/Plan_v0.3_Disaster_Recovery_WCSP.md, "Decisions")."""
    total_days = 0.0
    for damage_type, length_m in zip(members["damage_type"], members["length"]):
        rate = config.PRODUCTION_RATE_M_PER_DAY.get(damage_type, config.DEFAULT_PRODUCTION_RATE_M_PER_DAY)
        total_days += float(length_m) / rate
    return max(1, math.ceil(total_days))


def _pair_weight_lookup(region: RegionConfig) -> dict[str, float]:
    """pair_label ("Factory <-> Logistics") -> w_i * v_j, for impact_weight_k."""
    pairs = pd.read_csv(region.labels_dir / "factory_logistics_metapath_labels.csv")
    weight_by_name = compute_cluster_weights(region).set_index("name")["weight_km2"].to_dict()
    lookup: dict[str, float] = {}
    for row in pairs.itertuples():
        pair_label = f"{row.factory_name} <-> {row.logistics_name}"
        w_i = weight_by_name.get(row.factory_name, config.FALLBACK_MEMBER_AREA_KM2)
        v_j = weight_by_name.get(row.logistics_name, config.FALLBACK_MEMBER_AREA_KM2)
        lookup[pair_label] = w_i * v_j
    return lookup


def build_repair_lots(scenario: gpd.GeoDataFrame, region: RegionConfig) -> gpd.GeoDataFrame:
    """Group triggered (damaged) segments into connected-component Repair
    Lots and compute each lot's hard-constraint / cost / impact attributes."""
    triggered = scenario[scenario["damage_type"] != "none"].copy()
    if triggered.empty:
        return gpd.GeoDataFrame(columns=_LOT_COLUMNS + ["geometry"], geometry="geometry", crs=scenario.crs)

    trigger_ids = set(triggered["segment_id"])

    adjacency = nx.Graph()
    edge_to_segments: dict[frozenset, list[str]] = {}
    for row in scenario.itertuples():
        if row.segment_id in trigger_ids:
            adjacency.add_edge(row.u, row.v)
            key = frozenset((row.u, row.v))
            edge_to_segments.setdefault(key, []).append(row.segment_id)

    pair_weights = _pair_weight_lookup(region)
    segment_lookup = triggered.set_index("segment_id")

    lot_rows = []
    for lot_num, component in enumerate(nx.connected_components(adjacency)):
        comp_segment_ids: set[str] = set()
        for u, v in adjacency.subgraph(component).edges():
            comp_segment_ids.update(edge_to_segments.get(frozenset((u, v)), []))
        if not comp_segment_ids:
            continue

        members = segment_lookup.loc[sorted(comp_segment_ids)]
        corridor_counts = members["corridor_id"].value_counts()
        primary_corridor = corridor_counts.index[0] if len(corridor_counts) else None
        hard_disaster = bool(members["disaster_hard"].any())
        hard_metapath = bool(members["is_metapath_hard"].any())
        length_total_m = float(members["length"].sum())
        duration_days = _lot_duration_days(members)

        impact_weight_k = 0.0
        for pairs_str in members["metapath_pairs"]:
            if not pairs_str:
                continue
            for pair_label in pairs_str.split("; "):
                impact_weight_k += pair_weights.get(pair_label, 0.0)

        lot_rows.append(
            {
                "lot_id": f"L{lot_num}",
                "segment_ids": "; ".join(sorted(comp_segment_ids)),
                "n_segments": len(comp_segment_ids),
                "length_total_m": length_total_m,
                "duration_days": duration_days,
                "primary_corridor_id": primary_corridor,
                "hard_disaster": hard_disaster,
                "hard_metapath": hard_metapath,
                "hard": hard_disaster or hard_metapath,
                "cost_k": length_total_m,  # 1 metre == 1 cost unit (Decisions)
                "impact_weight_k": impact_weight_k,
                "geometry": members.geometry.union_all(),
            }
        )

    lots = gpd.GeoDataFrame(lot_rows, geometry="geometry", crs=scenario.crs)
    return lots


def export_lots(lots: gpd.GeoDataFrame, region: RegionConfig) -> None:
    out_dir = region.disaster_recovery_dir / "labels"
    out_dir.mkdir(parents=True, exist_ok=True)
    lots.drop(columns="geometry", errors="ignore").to_csv(
        out_dir / "disaster_repair_lots.csv", index=False, encoding="utf-8-sig"
    )
    if len(lots):
        lots.to_file(out_dir / "disaster_repair_lots.geojson", driver="GeoJSON")
