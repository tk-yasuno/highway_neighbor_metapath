"""End-to-end entry point for v0.3 Disaster Pavement Recovery Scheduling WCSP.

Run with:
    .venv-himet\\Scripts\\python.exe run_disaster_recovery.py [--region gurgaon]

Requires `run_pipeline.py --region <key>` to have already been run for the
target region: reads data/raw/<region>/highway_graph.graphml,
data/processed/<region>/clusters.geojson, and
outputs/<region>/labels/{factory_logistics_metapath_labels.csv,
segment_hard_constraint_flags.csv}.

Steps
-----
1. scenario      : synthetic earthquake damage per HighwaySegment
2. repair_lots   : bundle damaged segments into Repair Lots
3. loss          : Supply Chain Loss evaluator (eligible pairs, weights)
4. tabu_solver   : schedule Repair Lots over the recovery horizon
5. export        : schedule, SC_Loss time series, Recovery Benefit frontier
"""

from __future__ import annotations

import argparse

from src import regions
from src.disaster_recovery import config as dr_config
from src.disaster_recovery import export, repair_lots, scenario, tabu_solver
from src.disaster_recovery.loss import SCLossEvaluator


def _check_inputs(region: regions.RegionConfig) -> None:
    required = [
        region.data_raw_dir / "highway_graph.graphml",
        region.data_processed_dir / "clusters.geojson",
        region.labels_dir / "factory_logistics_metapath_labels.csv",
        region.labels_dir / "segment_hard_constraint_flags.csv",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit(
            f"Missing v0.1/v0.2 pipeline outputs for region '{region.key}':\n  "
            + "\n  ".join(missing)
            + f"\nRun `run_pipeline.py --region {region.key}` first."
        )


def main(
    region_key: str = "gurgaon",
    seed: int = dr_config.SCENARIO_SEED,
    horizon_days: int = dr_config.HORIZON_DAYS,
    daily_capacity: int = dr_config.DAILY_CREW_CAPACITY,
    max_iterations: int = dr_config.MAX_ITERATIONS,
):
    region = regions.get_region(region_key)
    _check_inputs(region)
    regions.ensure_dirs(region)

    print("=" * 70)
    print(f"v0.3 Disaster Pavement Recovery Scheduling WCSP -- {region.display_name}")
    print("=" * 70)

    print("Step 1/5: synthetic earthquake scenario generation")
    scenario_gdf = scenario.run_scenario(region, seed=seed)
    n_blocked = int((scenario_gdf["damage_type"] == "landslide_block").sum())
    n_cracked = int((scenario_gdf["damage_type"] == "crack_pothole").sum())
    print(f"  segments: {len(scenario_gdf)} | landslide_block: {n_blocked} | crack_pothole: {n_cracked}")

    print("Step 2/5: repair lot construction")
    lots = repair_lots.build_repair_lots(scenario_gdf, region)
    repair_lots.export_lots(lots, region)
    n_hard = int(lots["hard"].sum()) if len(lots) else 0
    print(f"  repair lots: {len(lots)} | hard (disaster-blocking or metapath-critical): {n_hard}")
    if n_hard:
        hard_lots = lots[lots["hard"]]
        total_crew_days = float(hard_lots["duration_days"].sum())
        max_duration = int(hard_lots["duration_days"].max())
        print(f"  hard-lot repair duration: total {total_crew_days:.0f} crew-days | longest single lot {max_duration} days")

    print("Step 3/5: supply-chain loss evaluator setup")
    evaluator = SCLossEvaluator(region)
    print(f"  eligible pairs: {len(evaluator.pairs)} (excluded as unreachable pre-disaster: {evaluator.n_excluded_unreachable})")

    print(f"Step 4/5: tabu search (horizon={horizon_days}d, capacity={daily_capacity}/day, max_iter={max_iterations})")
    solver = tabu_solver.TabuSolver(
        lots,
        scenario_gdf,
        evaluator,
        region,
        horizon_days=horizon_days,
        daily_capacity=daily_capacity,
        max_iterations=max_iterations,
    )
    result = solver.solve()

    print("Step 5/5: export schedule, metrics, figures")
    export.export_schedule(result, region)
    ts, _frontier, _convergence = export.export_metrics_and_figures(result, evaluator, region)

    v_hard = solver._v_hard(result.assignment)
    v_cap = solver._v_cap(result.assignment)
    total_rb = float((ts["sc_loss_no_repair"] - ts["sc_loss_with_repair"]).sum())
    total_no_repair = float(ts["sc_loss_no_repair"].sum())
    pct_reduction = (total_rb / total_no_repair * 100.0) if total_no_repair > 0 else 0.0

    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  hard lots: {len(solver.hard_lot_ids)} | scheduled within horizon: {len(solver.hard_lot_ids) - v_hard}")
    print(f"  v_hard={v_hard} (unscheduled hard lots)  v_cap={v_cap} (capacity overflow-days)")
    if result.history:
        print(f"  initial J(x)={result.history[0][1]:.4f}  final J(x)={result.history[-1][1]:.4f}")
    print(f"  cumulative Recovery Benefit over horizon: {total_rb:.4f} ({pct_reduction:.1f}% of no-repair SC_Loss)")
    print(f"  outputs -> {region.disaster_recovery_dir}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="gurgaon", choices=sorted(regions.REGIONS), help="region key (default: gurgaon)")
    parser.add_argument("--seed", type=int, default=dr_config.SCENARIO_SEED)
    parser.add_argument("--horizon-days", type=int, default=dr_config.HORIZON_DAYS)
    parser.add_argument("--daily-capacity", type=int, default=dr_config.DAILY_CREW_CAPACITY)
    parser.add_argument("--max-iterations", type=int, default=dr_config.MAX_ITERATIONS)
    args = parser.parse_args()
    main(
        region_key=args.region,
        seed=args.seed,
        horizon_days=args.horizon_days,
        daily_capacity=args.daily_capacity,
        max_iterations=args.max_iterations,
    )
