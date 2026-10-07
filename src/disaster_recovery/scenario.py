"""Phase 1 (v0.3): synthetic earthquake damage scenario generator.

No real seismic-hazard or landslide-susceptibility dataset is used here:
this module draws independent Bernoulli damage flags per HighwaySegment with
a fixed RNG seed, purely to exercise the Disaster Pavement Recovery
Scheduling WCSP end to end (see docs/Plan_v0.3_Disaster_Recovery_WCSP.md,
"Decisions"). Swapping in a real hazard layer later only requires replacing
``generate_segment_damage`` with a function that returns the same schema.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import regions
from ..highway_segments import build_segments_and_corridors
from ..regions import RegionConfig
from . import config


def generate_segment_damage(region: RegionConfig, seed: int = config.SCENARIO_SEED) -> gpd.GeoDataFrame:
    """Join every HighwaySegment with its v0.1/v0.2 metapath hard-constraint
    flag and assign a synthetic earthquake damage outcome.

    Returns a GeoDataFrame with all `build_segments_and_corridors` columns
    plus: metapath_priority, is_metapath_hard, metapath_pairs,
    damage_type ("none" / "crack_pothole" / "landslide_block"),
    initial_open (y_{s,0}), disaster_hard (Hard_k^disaster precursor).
    """
    segments, _corridor_geoms, _graph_u = build_segments_and_corridors(region)
    flags = pd.read_csv(region.labels_dir / "segment_hard_constraint_flags.csv")
    flags = flags[["segment_id", "metapath_priority", "is_metapath_hard", "metapath_pairs"]]

    scenario = segments.merge(flags, on="segment_id", how="left")
    scenario["is_metapath_hard"] = scenario["is_metapath_hard"].fillna(False).astype(bool)
    scenario["metapath_priority"] = scenario["metapath_priority"].fillna("None")
    scenario["metapath_pairs"] = scenario["metapath_pairs"].fillna("")

    rng = np.random.default_rng(seed)
    n = len(scenario)
    landslide_block = rng.random(n) < config.BLOCK_PROB
    crack_pothole = rng.random(n) < config.CRACK_PROB

    damage_type = np.full(n, "none", dtype=object)
    damage_type[crack_pothole] = "crack_pothole"
    damage_type[landslide_block] = "landslide_block"  # landslide takes priority if both roll true
    scenario["damage_type"] = damage_type

    # y_{s,0}: a landslide-blocked segment is impassable immediately after
    # the earthquake; crack/pothole-only segments remain passable (v0.3
    # scope simplification, see Decisions).
    scenario["initial_open"] = scenario["damage_type"] != "landslide_block"
    scenario["disaster_hard"] = ~scenario["initial_open"]

    return scenario


def run_scenario(region: RegionConfig, seed: int = config.SCENARIO_SEED) -> gpd.GeoDataFrame:
    """Generate the scenario and persist it (geojson under data/processed,
    flat CSV under the region's disaster_recovery/labels/ dir)."""
    regions.ensure_dirs(region)
    scenario = generate_segment_damage(region, seed=seed)

    out_geojson = region.data_processed_dir / "disaster_scenario_t0.geojson"
    scenario.to_file(out_geojson, driver="GeoJSON")

    out_csv = region.disaster_recovery_dir / "labels" / "disaster_segment_damage.csv"
    scenario.drop(columns="geometry").to_csv(out_csv, index=False, encoding="utf-8-sig")

    return scenario


if __name__ == "__main__":
    _region = regions.get_region("gurgaon")
    _scenario = run_scenario(_region)
    print(_scenario["damage_type"].value_counts().to_string())
    print(f"disaster_hard segments: {int(_scenario['disaster_hard'].sum())} / {len(_scenario)}")
    print(f"is_metapath_hard segments: {int(_scenario['is_metapath_hard'].sum())} / {len(_scenario)}")
