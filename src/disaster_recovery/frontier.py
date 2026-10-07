"""Phase 5 (v0.3): SC_Loss(t) time series and the Recovery Benefit vs
cumulative repair distance frontier (the
docs/ScenarioOptimization_20261007.jpg sketch: y=Recovery Benefit,
x=distance).
"""

from __future__ import annotations

import pandas as pd

from .tabu_solver import SolveResult, _blocked_at_day, _completion_day


def _no_repair_blocked(result: SolveResult) -> frozenset:
    if not result.blocking_segments_by_lot:
        return frozenset()
    return frozenset().union(*result.blocking_segments_by_lot.values())


def sc_loss_timeseries(result: SolveResult, evaluator) -> pd.DataFrame:
    """Per-day SC_Loss with the tabu-search schedule applied, vs. a
    no-repair baseline that holds the t=0 damage state fixed for the whole
    horizon. RB = sum_t(no_repair(t) - with_repair(t))."""
    no_repair_blocked = _no_repair_blocked(result)
    rows = []
    for day in range(1, result.horizon_days + 1):
        blocked_with_repair = _blocked_at_day(
            day, result.assignment, result.blocking_segments_by_lot, result.duration_by_lot
        )
        rows.append(
            {
                "day": day,
                "sc_loss_no_repair": evaluator.compute_sc_loss(no_repair_blocked),
                "sc_loss_with_repair": evaluator.compute_sc_loss(blocked_with_repair),
            }
        )
    return pd.DataFrame(rows)


def build_recovery_frontier(result: SolveResult, evaluator) -> pd.DataFrame:
    """Static, order-dependent "skyline" view (complementary to the dynamic
    `sc_loss_timeseries`): walk through the lots in the order they actually
    *finish* repair (start_day + duration_days - 1, not merely start), and
    accumulate (distance repaired, SC_Loss reduction achieved so far),
    mirroring the parent Repair Lot Skyline WCSP's cumulative skyline
    frontier (Definition 9-10). Ordering by completion rather than start
    matters once lots have realistic, varying multi-day durations: a long
    lot started early may finish after several short lots started later."""
    scheduled = [
        (lot_id, start_day, _completion_day(start_day, result.duration_by_lot.get(lot_id, 1)))
        for lot_id, start_day in result.assignment.items()
        if start_day is not None
    ]
    scheduled.sort(key=lambda item: (item[2], item[0]))  # (completion_day, lot_id)

    if not scheduled:
        return pd.DataFrame(
            columns=[
                "step",
                "lot_id",
                "day_scheduled",
                "day_completed",
                "cumulative_distance_m",
                "recovery_benefit_cumulative",
            ]
        )

    lots_by_id = result.lots.set_index("lot_id")
    no_repair_blocked = _no_repair_blocked(result)
    sc_loss_no_repair = evaluator.compute_sc_loss(no_repair_blocked)

    still_blocked = set(no_repair_blocked)
    cumulative_distance_m = 0.0
    rows = []
    for step, (lot_id, start_day, completion_day) in enumerate(scheduled, start=1):
        cumulative_distance_m += float(lots_by_id.loc[lot_id, "length_total_m"])
        still_blocked -= result.blocking_segments_by_lot.get(lot_id, frozenset())
        sc_loss_after = evaluator.compute_sc_loss(frozenset(still_blocked))
        rows.append(
            {
                "step": step,
                "lot_id": lot_id,
                "day_scheduled": start_day,
                "day_completed": completion_day,
                "cumulative_distance_m": cumulative_distance_m,
                "recovery_benefit_cumulative": sc_loss_no_repair - sc_loss_after,
            }
        )
    return pd.DataFrame(rows)
