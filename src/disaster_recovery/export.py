"""Phase 5 (v0.3): persist the schedule, metrics, and figures for a solved
Disaster Pavement Recovery Scheduling WCSP instance."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd

from ..regions import RegionConfig
from .frontier import build_recovery_frontier, sc_loss_timeseries
from .tabu_solver import SolveResult, _completion_day


def export_schedule(result: SolveResult, region: RegionConfig) -> pd.DataFrame:
    lots_by_id = result.lots.set_index("lot_id") if len(result.lots) else result.lots
    rows = []
    for lot_id in result.hard_lot_ids:
        start_day = result.assignment.get(lot_id)
        duration_days = result.duration_by_lot.get(lot_id, 1)
        rows.append(
            {
                "lot_id": lot_id,
                "day_scheduled": start_day,
                "duration_days": duration_days,
                "day_completed": _completion_day(start_day, duration_days),
                "cost_k": float(lots_by_id.loc[lot_id, "cost_k"]),
                "hard_disaster": bool(lots_by_id.loc[lot_id, "hard_disaster"]),
                "hard_metapath": bool(lots_by_id.loc[lot_id, "hard_metapath"]),
            }
        )
    df = pd.DataFrame(
        rows,
        columns=[
            "lot_id",
            "day_scheduled",
            "duration_days",
            "day_completed",
            "cost_k",
            "hard_disaster",
            "hard_metapath",
        ],
    )
    if len(df):
        df = df.sort_values(["day_scheduled", "lot_id"], na_position="last")
    out_dir = region.disaster_recovery_dir / "schedule"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "repair_schedule.csv", index=False, encoding="utf-8-sig")
    return df


def export_metrics_and_figures(result: SolveResult, evaluator, region: RegionConfig):
    metrics_dir = region.disaster_recovery_dir / "metrics"
    figures_dir = region.disaster_recovery_dir / "figures"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    ts = sc_loss_timeseries(result, evaluator)
    ts.to_csv(metrics_dir / "sc_loss_timeseries.csv", index=False, encoding="utf-8-sig")

    frontier = build_recovery_frontier(result, evaluator)
    frontier.to_csv(metrics_dir / "recovery_frontier.csv", index=False, encoding="utf-8-sig")

    convergence = pd.DataFrame(result.history, columns=["iteration", "J"])
    convergence.to_csv(metrics_dir / "tabu_convergence.csv", index=False, encoding="utf-8-sig")

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(ts["day"], ts["sc_loss_no_repair"], label="no repair", color="#b30000")
    ax.plot(ts["day"], ts["sc_loss_with_repair"], label="with repair (tabu schedule)", color="#2171b5")
    ax.set_xlabel("day")
    ax.set_ylabel("Supply Chain Loss (km$^2$-equivalent)")
    ax.set_title(f"{region.display_name}\nSC_Loss(t)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures_dir / "sc_loss_timeseries.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    if not frontier.empty:
        ax.step(
            frontier["cumulative_distance_m"] / 1000.0,
            frontier["recovery_benefit_cumulative"],
            where="post",
            color="#2171b5",
        )
    ax.set_xlabel("cumulative repaired distance (km)")
    ax.set_ylabel("Recovery Benefit (SC_Loss reduction)")
    ax.set_title(f"{region.display_name}\nDisaster Recovery Frontier")
    fig.tight_layout()
    fig.savefig(figures_dir / "recovery_frontier.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(convergence["iteration"], convergence["J"], color="#2171b5")
    ax.set_xlabel("tabu search iteration")
    ax.set_ylabel("J(x)")
    ax.set_title(f"{region.display_name}\nTabu search convergence")
    fig.tight_layout()
    fig.savefig(figures_dir / "tabu_convergence.png", dpi=150)
    plt.close(fig)

    return ts, frontier, convergence
