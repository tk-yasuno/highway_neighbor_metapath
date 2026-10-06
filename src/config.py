"""Configuration for the Highway-neighbor Metapath Labeling MVP (Gurgaon / Gurugram, India).

All constants used across the pipeline (bbox, CRS, OSM tags, seed place names,
clustering parameters, and metapath thresholds) live here so every module and
the CLI pipeline script stay in sync.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
FIGURES_DIR = OUTPUTS_DIR / "figures"
LABELS_DIR = OUTPUTS_DIR / "labels"

for _d in (DATA_RAW_DIR, DATA_PROCESSED_DIR, FIGURES_DIR, LABELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Spatial scope: bbox covering the Gurgaon (Gurugram) industrial / logistics
# supply-chain belt, per docs/Gurgaon_background_bbox_scale.txt
#   north-south : approx 25-30 km (Farrukhnagar/Udyog Vihar -> Bilaspur/Sohna)
#   east-west   : approx 35 km (Gurugram centre/Sohna Road -> Luhari)
# bbox given as (west, south, east, north) in WGS84 lon/lat degrees.
# ---------------------------------------------------------------------------
BBOX = (76.68, 28.25, 77.12, 28.52)  # west, south, east, north

CRS_WGS84 = "EPSG:4326"
CRS_METRIC = "EPSG:32643"  # UTM zone 43N, metres - used for all distance math

# ---------------------------------------------------------------------------
# Named seed places (geocoded via OSM Nominatim) used to label DBSCAN clusters
# with human-readable Industrial Park / Logistics District names instead of
# generic cluster ids. type is one of {"factory", "logistics"}.
# ---------------------------------------------------------------------------
SEED_PLACES = {
    "IMT Manesar": {"lat": 28.3671851, "lon": 76.920669, "type": "factory"},
    "Udyog Vihar": {"lat": 28.4917349, "lon": 77.0819718, "type": "factory"},
    "Sohna Industrial Area": {"lat": 28.3241636, "lon": 77.0848644, "type": "factory"},
    "Bilaspur Logistics Hub": {"lat": 28.2882747, "lon": 76.8704031, "type": "logistics"},
    "Farrukhnagar Logistics Hub": {"lat": 28.467225, "lon": 76.8253658, "type": "logistics"},
    "Luhari Logistics Estate": {"lat": 28.3732269, "lon": 76.7156143, "type": "logistics"},
}
SEED_MATCH_TOLERANCE_KM = 6.0  # max distance to snap a DBSCAN cluster to a named seed

REFERENCE_POINT = {"name": "Gurugram Centre", "lat": 28.4646148, "lon": 77.0299194}

# ---------------------------------------------------------------------------
# OSM tags (Step 1 of the MVP labeling rule)
# ---------------------------------------------------------------------------
HIGHWAY_CUSTOM_FILTER = (
    '["highway"~"motorway|trunk|primary|motorway_link|trunk_link|primary_link"]'
)
INDUSTRIAL_TAGS = {"landuse": "industrial"}
WAREHOUSE_TAGS_LIST = [{"building": "warehouse"}, {"industrial": "warehouse"}]

# ---------------------------------------------------------------------------
# Clustering (DBSCAN over projected centroids, metres)
# ---------------------------------------------------------------------------
DBSCAN_EPS_M = 1500.0
DBSCAN_MIN_SAMPLES = 2

# ---------------------------------------------------------------------------
# Metapath labeling thresholds
# ---------------------------------------------------------------------------
NEIGHBOR_RADII_KM = [2.0, 3.0, 4.0, 5.0]  # r_h candidates for NEAR_HIGHWAY / H_NEIGHBOR(r)
CHAIN_DISTANCE_THRESHOLD_KM = 40.0  # upper bound for H_CHAIN_ACCESS road-network distance

# Priority tiers used to feed the hard-constraint flag back into
# method_repair_lot_wcsp (see README.md "Integration" section).
PRIORITY_HIGH_CHAIN_KM = 25.0
PRIORITY_HIGH_NEIGHBOR_KM = 3.0
