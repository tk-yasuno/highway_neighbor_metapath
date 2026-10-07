"""Phase 3 (v0.3): FactoryCluster / LogisticsCluster supply-chain weights.

w_i (factory) and v_j (logistics) are proportional to the cluster's real OSM
footprint area (``clusters.geojson``'s ``area_m2`` column, added in v0.3
Phase 0 -- see ``src/clustering.py``), per the concept note's "製造・出荷量は
工業ポリゴンの面積に比例する" assumption. Clusters with zero real polygon
area (e.g. a future region/dataset where warehouses are bare OSM nodes) fall
back to a small nominal area so they still carry a non-zero, bounded weight.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from ..regions import RegionConfig
from . import config


def compute_cluster_weights(region: RegionConfig) -> pd.DataFrame:
    """Return one row per FactoryCluster/LogisticsCluster with its
    supply-chain weight in km^2 (w_i for factory, v_j for logistics)."""
    clusters_path = region.data_processed_dir / "clusters.geojson"
    clusters = gpd.read_file(clusters_path)
    if "area_m2" not in clusters.columns:
        raise ValueError(
            f"{clusters_path} has no 'area_m2' column. Re-run "
            f"`run_pipeline.py --region {region.key}` with the v0.3-patched "
            "src/clustering.py to regenerate clusters.geojson."
        )

    fallback_m2 = config.FALLBACK_MEMBER_AREA_KM2 * config.AREA_UNIT_DIVISOR
    area_m2 = clusters["area_m2"].fillna(0.0).clip(lower=0.0)
    weight_km2 = area_m2.where(area_m2 > 0.0, fallback_m2) / config.AREA_UNIT_DIVISOR

    return pd.DataFrame(
        {
            "cluster_id": clusters["cluster_id"],
            "name": clusters["name"],
            "node_type": clusters["node_type"],
            "area_m2": area_m2,
            "weight_km2": weight_km2,
        }
    )
