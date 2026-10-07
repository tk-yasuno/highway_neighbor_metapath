"""Run the v0.3 Disaster Pavement Recovery Scheduling WCSP for all four
regions in sequence: Gurgaon first, then the v0.2 generality case studies
(Stuttgart, Taoyuan-Hsinchu, Nagoya-Toyota-Komaki).

Run with:
    .venv-himet\\Scripts\\python.exe run_disaster_recovery_all_regions.py

Each region must already have `run_pipeline.py --region <key>` outputs on
disk; see run_disaster_recovery.py for the per-region entry point and CLI
options (this wrapper always uses the default parameters).
"""

from __future__ import annotations

from run_disaster_recovery import main

REGION_ORDER = ["gurgaon", "stuttgart", "taoyuan_hsinchu", "nagoya_toyota_komaki"]

if __name__ == "__main__":
    results = {}
    for key in REGION_ORDER:
        print("\n" + "#" * 70)
        print(f"# region: {key}")
        print("#" * 70)
        results[key] = main(region_key=key)
