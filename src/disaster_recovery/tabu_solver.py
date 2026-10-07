"""Phase 4 (v0.3): Tabu search solver for the Disaster Pavement Recovery
Scheduling WCSP.

Solution representation: ``assignment: dict[lot_id, int | None]``, the
**start day** (1..T) each *hard* Repair Lot (``hard_disaster`` OR
``hard_metapath``) begins repair on, or ``None`` if never started within
the horizon. Soft (crack-only, non-metapath-critical) lots never appear in
`J(x)` in this version -- the objective in
docs/Plan_v0.3_Disaster_Recovery_WCSP.md has no crack-benefit term, so
including them would only ever add cost for zero benefit -- and are
therefore excluded from the search space entirely (see "Decisions").

Resource model: `daily_capacity` is the number of crews working
*concurrently*, not a lots/day quota. Once started, a lot occupies one crew
for its full ``duration_days`` (length / damage-type production rate, see
``repair_lots.py``) before the segments it unblocks reopen -- a 5.8 km
landslide-blocked lot realistically takes ~58 days at 100 m/day, not 1. This
replaces an earlier "1 lot = 1 day, capacity = lots/day" version that made
every region converge to the same ~93% Recovery Benefit within 2 days
regardless of real lot length (see Plan "Decisions" for the full rationale).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config
from .loss import SCLossEvaluator


@dataclass
class SolveResult:
    assignment: dict  # lot_id -> start_day | None
    history: list  # list[(iteration, J)]
    lots: pd.DataFrame
    hard_lot_ids: list
    blocking_segments_by_lot: dict
    duration_by_lot: dict
    horizon_days: int
    daily_capacity: dict


def _capacity_schedule(horizon_days: int, daily_capacity) -> dict[int, int]:
    if isinstance(daily_capacity, dict):
        return {t: daily_capacity.get(t, config.DAILY_CREW_CAPACITY) for t in range(1, horizon_days + 1)}
    return {t: daily_capacity for t in range(1, horizon_days + 1)}


def _blocking_segments_by_lot(hard_lots: pd.DataFrame, scenario) -> dict:
    """lot_id -> frozenset of its member segment_ids that are actually
    landslide-blocked (crack/pothole-only members never block traffic)."""
    blocking_segment_ids = set(scenario.loc[scenario["damage_type"] == "landslide_block", "segment_id"])
    out = {}
    for row in hard_lots.itertuples():
        ids = set(row.segment_ids.split("; ")) if row.segment_ids else set()
        out[row.lot_id] = frozenset(ids & blocking_segment_ids)
    return out


def _completion_day(start_day, duration_days: int):
    if start_day is None:
        return None
    return start_day + duration_days - 1


def _blocked_at_day(day: int, assignment: dict, blocking_by_lot: dict, duration_by_lot: dict) -> frozenset:
    """Segments still closed at day `day`: a lot started on `start_day`
    occupies its crew (and keeps its segments closed) through
    `start_day + duration_days - 1` inclusive, reopening from the next day."""
    blocked: set = set()
    for lot_id, seg_ids in blocking_by_lot.items():
        if not seg_ids:
            continue
        start_day = assignment.get(lot_id)
        completion_day = _completion_day(start_day, duration_by_lot.get(lot_id, 1))
        if completion_day is None or day <= completion_day:
            blocked.update(seg_ids)
    return frozenset(blocked)


class TabuSolver:
    def __init__(
        self,
        lots: pd.DataFrame,
        scenario,
        evaluator: SCLossEvaluator,
        region,
        horizon_days: int = config.HORIZON_DAYS,
        daily_capacity=config.DAILY_CREW_CAPACITY,
        max_iterations: int = config.MAX_ITERATIONS,
        no_improve_limit: int = config.NO_IMPROVE_LIMIT,
        seed: int = config.SOLVER_SEED,
    ):
        self.lots = lots
        self.evaluator = evaluator
        self.region = region
        self.horizon_days = horizon_days
        self.capacity = _capacity_schedule(horizon_days, daily_capacity)
        # the greedy initial construction uses a fixed-size crew pool (list
        # scheduling onto identical parallel machines); day-varying capacity
        # overrides are still honoured by the v_cap penalty during search.
        self.crew_count = max(self.capacity.values()) if self.capacity else int(daily_capacity)
        self.max_iterations = max_iterations
        self.no_improve_limit = no_improve_limit
        self.rng = np.random.default_rng(seed)

        self.hard_lots = lots[lots["hard"]].reset_index(drop=True) if len(lots) else lots
        self.hard_lot_ids = list(self.hard_lots["lot_id"]) if len(self.hard_lots) else []
        self.cost_by_lot = dict(zip(self.hard_lots["lot_id"], self.hard_lots["cost_k"])) if len(self.hard_lots) else {}
        self.impact_by_lot = (
            dict(zip(self.hard_lots["lot_id"], self.hard_lots["impact_weight_k"])) if len(self.hard_lots) else {}
        )
        self.duration_by_lot = (
            dict(zip(self.hard_lots["lot_id"], self.hard_lots["duration_days"])) if len(self.hard_lots) else {}
        )
        self.blocking_by_lot = _blocking_segments_by_lot(self.hard_lots, scenario) if len(self.hard_lots) else {}

        self.total_cost_all_hard = float(self.hard_lots["cost_k"].sum()) if len(self.hard_lots) else 0.0
        self.total_cost_all_hard = self.total_cost_all_hard or 1.0
        all_blocking = frozenset().union(*self.blocking_by_lot.values()) if self.blocking_by_lot else frozenset()
        self.worst_case_sc_loss = self.evaluator.compute_sc_loss(all_blocking)
        self.f1_scale = max(self.worst_case_sc_loss * self.horizon_days, 1e-9)

    # -- objective components -------------------------------------------------
    def _f1(self, assignment: dict) -> float:
        total = 0.0
        for day in range(1, self.horizon_days + 1):
            blocked = _blocked_at_day(day, assignment, self.blocking_by_lot, self.duration_by_lot)
            total += self.evaluator.compute_sc_loss(blocked)
        return total

    def _f2(self, assignment: dict) -> float:
        return sum(self.cost_by_lot[lot_id] for lot_id, day in assignment.items() if day is not None)

    def _v_hard(self, assignment: dict) -> int:
        return sum(1 for lot_id in self.hard_lot_ids if assignment.get(lot_id) is None)

    def _v_cap(self, assignment: dict) -> int:
        """Overflow of concurrently in-progress lots (start_day <= t <=
        completion_day) above the day's crew capacity, summed over the
        horizon -- not just a same-day lot count."""
        load = {t: 0 for t in range(1, self.horizon_days + 1)}
        for lot_id, start_day in assignment.items():
            if start_day is None:
                continue
            completion_day = _completion_day(start_day, self.duration_by_lot.get(lot_id, 1))
            lo = max(1, start_day)
            hi = min(self.horizon_days, completion_day)
            for t in range(lo, hi + 1):
                load[t] += 1
        return sum(max(0, load[t] - self.capacity[t]) for t in range(1, self.horizon_days + 1))

    def objective(self, assignment: dict) -> float:
        f1_norm = self._f1(assignment) / self.f1_scale
        f2_norm = self._f2(assignment) / self.total_cost_all_hard
        v_hard = self._v_hard(assignment)
        v_cap = self._v_cap(assignment)
        return (
            config.ALPHA_SC_LOSS * f1_norm
            + config.ALPHA_COST * f2_norm
            + config.BETA_HARD * v_hard
            + config.BETA_CAP * v_cap
        )

    # -- initial solution -------------------------------------------------
    def _greedy_initial(self) -> dict:
        """List scheduling onto `self.crew_count` identical parallel crews:
        each lot (most urgent first) goes to whichever crew frees up
        earliest; a lot whose earliest possible start would fall after the
        horizon is left unscheduled (None), contributing to v_hard rather
        than silently running past the accounting window."""

        def sort_key(lot_id):
            is_blocking = 1 if self.blocking_by_lot.get(lot_id) else 0
            return (-is_blocking, -self.impact_by_lot.get(lot_id, 0.0), self.duration_by_lot.get(lot_id, 1))

        ordered = sorted(self.hard_lot_ids, key=sort_key)
        assignment = {lot_id: None for lot_id in self.hard_lot_ids}
        crew_free_day = [1] * max(self.crew_count, 1)
        for lot_id in ordered:
            crew_idx = min(range(len(crew_free_day)), key=lambda i: crew_free_day[i])
            start_day = crew_free_day[crew_idx]
            if start_day > self.horizon_days:
                break  # every crew is already busy past the horizon
            assignment[lot_id] = start_day
            crew_free_day[crew_idx] = start_day + self.duration_by_lot.get(lot_id, 1)
        return assignment

    # -- tabu search --------------------------------------------------------
    def solve(self) -> SolveResult:
        assignment = self._greedy_initial()
        best_assignment = dict(assignment)
        current_j = self.objective(assignment)
        best_j = current_j
        history = [(0, current_j)]

        if not self.hard_lot_ids:
            return SolveResult(
                assignment=best_assignment,
                history=history,
                lots=self.lots,
                hard_lot_ids=self.hard_lot_ids,
                blocking_segments_by_lot=self.blocking_by_lot,
                duration_by_lot=self.duration_by_lot,
                horizon_days=self.horizon_days,
                daily_capacity=self.capacity,
            )

        tabu_until: dict[tuple, int] = {}
        no_improve = 0
        all_days = np.arange(1, self.horizon_days + 1)

        for iteration in range(1, self.max_iterations + 1):
            sample_size = min(config.NEIGHBORHOOD_SAMPLE_SIZE, len(self.hard_lot_ids))
            candidate_lots = self.rng.choice(self.hard_lot_ids, size=sample_size, replace=False)

            best_move = None
            best_move_j = None
            for lot_id in candidate_lots:
                current_day = assignment[lot_id]
                n_days = min(config.CANDIDATE_DAYS_PER_LOT, self.horizon_days)
                sampled_days = list(self.rng.choice(all_days, size=n_days, replace=False))
                candidate_new_days = sampled_days + [None]

                for new_day in candidate_new_days:
                    if new_day == current_day:
                        continue
                    trial = dict(assignment)
                    trial[lot_id] = new_day
                    j = self.objective(trial)
                    move_sig = (lot_id, current_day, new_day)
                    is_tabu = tabu_until.get(move_sig, -1) >= iteration
                    if is_tabu and j >= best_j:
                        continue
                    if best_move_j is None or j < best_move_j:
                        best_move_j = j
                        best_move = (lot_id, current_day, new_day)

            if best_move is None:
                break

            lot_id, old_day, new_day = best_move
            assignment[lot_id] = new_day
            current_j = best_move_j
            tabu_until[(lot_id, new_day, old_day)] = iteration + config.TABU_TENURE
            history.append((iteration, current_j))

            if current_j < best_j - 1e-12:
                best_j = current_j
                best_assignment = dict(assignment)
                no_improve = 0
            else:
                no_improve += 1
            if no_improve >= self.no_improve_limit:
                break

        return SolveResult(
            assignment=best_assignment,
            history=history,
            lots=self.lots,
            hard_lot_ids=self.hard_lot_ids,
            blocking_segments_by_lot=self.blocking_by_lot,
            duration_by_lot=self.duration_by_lot,
            horizon_days=self.horizon_days,
            daily_capacity=self.capacity,
        )
