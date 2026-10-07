"""Region abstraction (v0.2): lets the pipeline run against any bbox/CRS/
clustering-parameter set instead of the single hardcoded Gurgaon region in
``config.py``.

``RegionConfig`` bundles everything the pipeline needs per region. The
``gurgaon`` entry is built directly from ``config.py``'s existing constants
and marked ``is_default=True`` so its output paths stay exactly the flat
``data/raw/`` / ``outputs/labels/`` layout already published and referenced
by the arXiv paper (backward compatibility). New regions (added for the
v0.2 generality case studies) get a ``data/raw/<key>/`` style subdirectory
and no named seed places: they are evaluated purely on DBSCAN auto-detected
clusters, without any manual facility curation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass(frozen=True)
class RegionConfig:
    key: str
    display_name: str
    bbox: tuple[float, float, float, float]  # west, south, east, north (WGS84)
    crs_metric: str
    seed_places: dict = field(default_factory=dict)
    seed_match_tolerance_km: float = 6.0
    dbscan_eps_m: float = 1500.0
    dbscan_min_samples: int = 2
    neighbor_radii_km: tuple[float, ...] = (2.0, 3.0, 4.0, 5.0)
    chain_distance_threshold_km: float = 40.0
    priority_high_chain_km: float = 25.0
    priority_high_neighbor_km: float = 3.0
    is_default: bool = False

    @property
    def data_raw_dir(self) -> Path:
        return config.DATA_RAW_DIR if self.is_default else config.DATA_RAW_DIR / self.key

    @property
    def data_processed_dir(self) -> Path:
        return config.DATA_PROCESSED_DIR if self.is_default else config.DATA_PROCESSED_DIR / self.key

    @property
    def figures_dir(self) -> Path:
        return config.FIGURES_DIR if self.is_default else config.OUTPUTS_DIR / self.key / "figures"

    @property
    def labels_dir(self) -> Path:
        return config.LABELS_DIR if self.is_default else config.OUTPUTS_DIR / self.key / "labels"

    @property
    def map_basename(self) -> str:
        return "gurgaon_highway_metapath_map" if self.is_default else f"{self.key}_highway_metapath_map"

    @property
    def disaster_recovery_dir(self) -> Path:
        """Root output directory for the v0.3 Disaster Pavement Recovery
        Scheduling WCSP (synthetic earthquake scenario, repair lots,
        tabu-search schedule, Recovery Benefit frontier). Follows the same
        is_default flat-path backward-compatibility pattern as labels_dir /
        figures_dir: gurgaon keeps outputs/disaster_recovery/, other regions
        get outputs/<key>/disaster_recovery/."""
        return config.OUTPUTS_DIR / "disaster_recovery" if self.is_default else config.OUTPUTS_DIR / self.key / "disaster_recovery"


def ensure_dirs(region: RegionConfig) -> None:
    for d in (region.data_raw_dir, region.data_processed_dir, region.figures_dir, region.labels_dir):
        d.mkdir(parents=True, exist_ok=True)
    for sub in ("labels", "schedule", "metrics", "figures"):
        (region.disaster_recovery_dir / sub).mkdir(parents=True, exist_ok=True)


REGIONS: dict[str, RegionConfig] = {
    "gurgaon": RegionConfig(
        key="gurgaon",
        display_name="Gurgaon (Gurugram), India - NH48 industrial-logistics belt",
        bbox=config.BBOX,
        crs_metric=config.CRS_METRIC,
        seed_places=config.SEED_PLACES,
        seed_match_tolerance_km=config.SEED_MATCH_TOLERANCE_KM,
        dbscan_eps_m=config.DBSCAN_EPS_M,
        dbscan_min_samples=config.DBSCAN_MIN_SAMPLES,
        neighbor_radii_km=tuple(config.NEIGHBOR_RADII_KM),
        chain_distance_threshold_km=config.CHAIN_DISTANCE_THRESHOLD_KM,
        priority_high_chain_km=config.PRIORITY_HIGH_CHAIN_KM,
        priority_high_neighbor_km=config.PRIORITY_HIGH_NEIGHBOR_KM,
        is_default=True,
    ),
    # --- v0.2 Supplementary generality case studies -----------------------
    # No named seed places: evaluated purely on DBSCAN auto-detected
    # clusters, using the *same* clustering/threshold defaults as Gurgaon,
    # to test transferability of the method without region-specific tuning.
    "stuttgart": RegionConfig(
        key="stuttgart",
        display_name="Stuttgart-Ludwigsburg Core Belt, Germany (A8/A81 belt)",
        bbox=(9.05, 48.70, 9.35, 49.00),  # ~33.4km (N-S) x 22.0km (E-W)
        crs_metric="EPSG:32632",  # UTM zone 32N
    ),
    "taoyuan_hsinchu": RegionConfig(
        key="taoyuan_hsinchu",
        display_name="Taoyuan-Zhongli-Hsinchu Corridor Core, Taiwan (Freeway 1 belt)",
        bbox=(121.00, 24.70, 121.30, 25.05),  # ~39.0km (N-S) x 30.3km (E-W)
        crs_metric="EPSG:32651",  # UTM zone 51N
    ),
    "nagoya_toyota_komaki": RegionConfig(
        key="nagoya_toyota_komaki",
        display_name="Nagoya-Toyota-Komaki Core Belt, Japan (Tomei/Meishin/Isewangan belt)",
        bbox=(136.80, 35.00, 137.10, 35.30),  # ~33.4km (N-S) x 27.3km (E-W)
        crs_metric="EPSG:32653",  # UTM zone 53N
    ),
}


def get_region(key: str) -> RegionConfig:
    try:
        return REGIONS[key]
    except KeyError:
        raise ValueError(f"Unknown region '{key}'. Available: {sorted(REGIONS)}") from None
