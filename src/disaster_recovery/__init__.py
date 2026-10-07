"""v0.3: Disaster Pavement Recovery Scheduling WCSP.

Consumes the v0.1/v0.2 Highway-neighbor Metapath Labeling outputs
(``outputs/<region>/labels/segment_hard_constraint_flags.csv`` and
``factory_logistics_metapath_labels.csv``) to simulate a synthetic
earthquake scenario, bundle damaged HighwaySegments into Repair Lots, and
schedule their recovery with a tabu search that minimizes a Supply Chain
Loss + construction-distance objective subject to the earthquake's and the
metapath's hard constraints.

See docs/Plan_v0.3_Disaster_Recovery_WCSP.md for the full design.
"""
