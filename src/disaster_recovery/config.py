"""Configuration for the v0.3 Disaster Pavement Recovery Scheduling WCSP.

Parameters here are deliberately region-independent defaults (the same
earthquake-damage probabilities, crew capacity, and tabu-search
hyperparameters are applied identically across Gurgaon and the three v0.2
generality case studies -- Stuttgart, Taoyuan-Hsinchu, Nagoya-Toyota-Komaki),
consistent with v0.2's "same thresholds, no re-calibration" transferability
test. Only output *paths* vary by region, via
``RegionConfig.disaster_recovery_dir`` (src/regions.py).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Synthetic earthquake scenario (src/disaster_recovery/scenario.py)
# No real seismic-hazard / landslide-susceptibility dataset is used; see
# docs/Plan_v0.3_Disaster_Recovery_WCSP.md "Decisions" for the rationale.
# ---------------------------------------------------------------------------
SCENARIO_SEED = 42
BLOCK_PROB = 0.04  # P(landslide_block, i.e. impassable) per HighwaySegment
CRACK_PROB = 0.08  # P(crack_pothole, non-blocking) per HighwaySegment

# ---------------------------------------------------------------------------
# Repair-lot / supply-chain weighting (src/disaster_recovery/weights.py)
# ---------------------------------------------------------------------------
AREA_UNIT_DIVISOR = 1e6  # m^2 -> km^2
FALLBACK_MEMBER_AREA_KM2 = 0.01  # used if a cluster has zero real polygon area

# ---------------------------------------------------------------------------
# Supply Chain Loss evaluator (src/disaster_recovery/loss.py)
# ---------------------------------------------------------------------------
LOSS_DISTANCE_SLACK_FACTOR = 1.5  # allowed_km = max(chain_distance_threshold_km, base_km * slack)

# ---------------------------------------------------------------------------
# Repair duration (src/disaster_recovery/repair_lots.py)
# A HighwaySegment's physical repair rate depends heavily on its damage
# type: landslide-block clearance/reconstruction is heavy-equipment work,
# crack/pothole patching is comparatively quick. A Repair Lot's duration is
# the sum, over its member segments, of length / rate(damage_type), rounded
# up to whole crew-days (min 1 day) -- NOT a fixed "1 lot = 1 day" (see
# docs/Plan_v0.3_Disaster_Recovery_WCSP.md, "Decisions" for why this
# replaced the original lots/day capacity model).
# ---------------------------------------------------------------------------
PRODUCTION_RATE_M_PER_DAY = {
    "landslide_block": 100.0,  # heavy-equipment debris clearance / reconstruction
    "crack_pothole": 500.0,  # patching / resurfacing
}
DEFAULT_PRODUCTION_RATE_M_PER_DAY = 300.0  # fallback for an unexpected damage_type

# ---------------------------------------------------------------------------
# Scheduling horizon and crew resources
# ---------------------------------------------------------------------------
HORIZON_DAYS = 60  # T -- a major-earthquake highway recovery realistically spans weeks/months, not days
DAILY_CREW_CAPACITY = 4  # number of crews working concurrently (each occupies 1 crew for a lot's full duration)

# ---------------------------------------------------------------------------
# Tabu search (src/disaster_recovery/tabu_solver.py)
# ---------------------------------------------------------------------------
SOLVER_SEED = SCENARIO_SEED + 1
TABU_TENURE = 15
MAX_ITERATIONS = 300
NO_IMPROVE_LIMIT = 60
NEIGHBORHOOD_SAMPLE_SIZE = 12  # lots sampled per iteration (not exhaustive neighborhood)
CANDIDATE_DAYS_PER_LOT = 5  # random target days tried per sampled lot per iteration

# ---------------------------------------------------------------------------
# Objective weights: J(x) = a1*f1_norm + a2*f2_norm + beta_hard*v_hard + beta_cap*v_cap
# (f1, f2 are min-max-style normalized to [0,1] before weighting; see
# tabu_solver.TabuSolver.objective)
# ---------------------------------------------------------------------------
ALPHA_SC_LOSS = 1.0  # alpha1 (Supply Chain Loss)
ALPHA_COST = 1.0  # alpha2 (construction distance / cost)
BETA_HARD = 100.0  # hard-constraint violation penalty (>> alphas)
BETA_CAP = 20.0  # daily-capacity violation penalty (<< beta_hard, >> alphas)
