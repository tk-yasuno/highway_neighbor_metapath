# Highway-neighbor Metapath Labeling between Industrial Park and Logistics District

MVP implementation for the Gurgaon (Gurugram, India) supply-chain belt: labels
pairs of **Industrial Park** (FactoryCluster) and **Logistics District**
(LogisticsCluster) nodes with a "Highway-neighbor metapath" whenever they are
linked through the regional highway network (NH48 and friends), so that a
construction/repair closure on that stretch of highway can be understood as
affecting a specific Factory <-> Logistics supply-chain link.

Concept source: [docs/ConceptNete_highway_access_metapath_261006.jpg](docs/ConceptNete_highway_access_metapath_261006.jpg),
[docs/Gurgaon_background_bbox_scale.txt](docs/Gurgaon_background_bbox_scale.txt).
Downstream consumer: [docs/method_repair_lot_wcsp.pdf](docs/method_repair_lot_wcsp.pdf)
(Repair Lot Skyline WCSP) — see "Integration" below.

## Environment

All computation runs in the `.venv-himet` virtual environment (Python 3.12):

```powershell
python -m venv .venv-himet
.venv-himet\Scripts\python.exe -m pip install -r requirements.txt
```

Key packages: `geopandas`, `osmnx`, `shapely`, `networkx`, `folium`,
`scikit-learn`, `scipy`, `pandas`, `matplotlib`. OSM data is fetched live from
the public Overpass API on first run and cached under `data/raw/`.

## Running the pipeline

```powershell
.venv-himet\Scripts\python.exe run_pipeline.py            # uses the OSM cache if present
.venv-himet\Scripts\python.exe run_pipeline.py --force-refetch   # re-download from Overpass
```

Each step can also be run standalone as a module (`python -m src.<name>`):
`osm_fetch`, `clustering`, `highway_segments`, `metapath`, `label_export`,
`visualize`.

## Pipeline / graph schema

| Step | Module | Output |
|---|---|---|
| 1. Fetch OSM | `src/osm_fetch.py` | `data/raw/highway_graph.graphml`, `industrial_polygons.geojson`, `warehouse_features.geojson` |
| 2. Cluster | `src/clustering.py` | `data/processed/clusters.geojson` — **FactoryCluster** / **LogisticsCluster** nodes (DBSCAN over OSM `landuse=industrial` + `building=warehouse`/`industrial=warehouse` centroids, snapped to the 6 named seed places within 6 km) |
| 3. Highway topology | `src/highway_segments.py` | `data/processed/highway_segments.geojson` — **HighwaySegment** edges (OSM `motorway/trunk/primary(+_link)`), grouped into named **corridors** via `ref`/`name` (NH48, SH15A, NH352W, Sohna Road, ...); undirected routable graph for `HIGHWAY_CONTIGUOUS` |
| 4. Metapath labeling | `src/metapath.py` | `NEAR_HIGHWAY(cluster, corridor)` edges; `H_NEIGHBOR(r)` and `H_CHAIN_ACCESS(distance)` metapaths |
| 5. Export | `src/label_export.py` | `outputs/labels/factory_logistics_metapath_labels.csv`, `segment_hard_constraint_flags.{csv,geojson}` |
| 6. Visualize | `src/visualize.py` | `outputs/figures/gurgaon_highway_metapath_map.{png,html}` |

Node/edge types follow the MVP schema in the concept note:

* **FactoryCluster** / **LogisticsCluster** — DBSCAN clusters (`eps=1.5 km`,
  `min_samples=2`) of OSM industrial/warehouse centroids; a cluster is
  "named" (`is_named_seed=True`) if it snaps within 6 km of one of the 6
  seed places (IMT Manesar, Udyog Vihar, Sohna Industrial Area; Bilaspur,
  Farrukhnagar, Luhari Logistics Estate). Un-named clusters are kept (as
  `FactoryCluster_auto_N` / `LogisticsCluster_auto_N`) for completeness but
  are excluded from the hard-constraint export (see below).
* **HighwaySegment** — one row per OSM way edge on `motorway/trunk/primary`
  (+ `_link` ramps), grouped into a **corridor** by `ref` (preferred, e.g.
  `NH48`) or `name` (e.g. `Sohna Road`).
* `NEAR_HIGHWAY(cluster, corridor)` — cluster centroid to corridor geometry
  distance ≤ 5 km (the largest configured radius).
* `HIGHWAY_CONTIGUOUS` — implicit in the routable road graph (shared OSM
  nodes); used for shortest-path `H_CHAIN_ACCESS` queries.

### Metapath labels

1. **`H_NEIGHBOR(r)`** — FactoryCluster –NEAR_HIGHWAY→ corridor
   ←NEAR_HIGHWAY– LogisticsCluster: both endpoints are within the *same*
   radius tier `r ∈ {2, 3, 4, 5} km` of the same named corridor. Labeled with
   the smallest qualifying tier.
2. **`H_CHAIN_ACCESS(distance)`** — shortest `HIGHWAY_CONTIGUOUS` road-network
   path length between the nearest highway node to each cluster, labeled
   when `distance ≤ 40 km` (`config.CHAIN_DISTANCE_THRESHOLD_KM`).

Priority tiers (`src/config.py`): **High** if `r ≤ 3 km` or
`chain distance ≤ 25 km`; **Medium** if either label applies at all;
**None** otherwise.

## Key MVP findings (Gurgaon bbox, 76.68–77.12°E, 28.25–28.52°N)

| Factory | Logistics | Road distance | Priority | Labels |
|---|---|---|---|---|
| IMT Manesar | Bilaspur Logistics Hub | 16.3 km | **High** | H_NEIGHBOR(r=3km); H_CHAIN_ACCESS(16.3km) |
| Udyog Vihar | Bilaspur Logistics Hub | 35.1 km | **High** | H_NEIGHBOR(r=2km); H_CHAIN_ACCESS(35.1km) |
| IMT Manesar | Farrukhnagar Logistics Hub | 33.1 km | Medium | H_CHAIN_ACCESS(33.1km) |
| Sohna Industrial Area | Farrukhnagar Logistics Hub | 37.7 km | Medium | H_CHAIN_ACCESS(37.7km) |
| IMT Manesar | Luhari Logistics Estate | 40.1 km | — | (just over the 40 km chain threshold; Luhari sits ~11 km from the nearest motorway/trunk/primary way in OSM, reached via MDR-132/Pataudi Road rather than directly off NH48) |

See `outputs/labels/factory_logistics_metapath_labels.csv` for the full
122-row table (named + auto clusters) and
`outputs/labels/segment_hard_constraint_flags.csv` for the per-`HighwaySegment`
flag (34 segments / ~24 km of NH48 flagged `is_metapath_hard=True`).

Maps: `outputs/figures/gurgaon_highway_metapath_map.png` (static, named-pair
links only) and `.html` (interactive Folium map with full highway network,
cluster markers, 2/3/4/5 km buffers, and metapath routes).

## Integration with `method_repair_lot_wcsp` (hard-constraint extension)

`method_repair_lot_wcsp.pdf` defines the repair-lot inclusion decision
(Definition 8) as

```
d_j = include   if Hard_j = 1                        (pothole hard constraint)
    = include   if Hard_j = 0 and Benefit_j >= Cost_j  (crack soft constraint)
    = exclude   otherwise
```

where `Hard_j = 1[P_j > 0]` (cluster `j` contains a pothole). This MVP's
output is designed to extend that hard-trigger with a **metapath hard flag**:
a repair lot whose chainage range overlaps a `HighwaySegment` flagged
`is_metapath_hard=True` in `segment_hard_constraint_flags.csv` should also be
forced into the repair plan, i.e.

```
Hard_j = 1[P_j > 0]  OR  1[lot_j overlaps a segment with is_metapath_hard = True]
```

Only segments within the matched `H_NEIGHBOR` radius of a **named**
Factory<->Logistics pair (e.g. the ~24 km of NH48 bracketing IMT Manesar and
Bilaspur/Udyog Vihar) are flagged `is_metapath_hard=True`; the broader
`H_CHAIN_ACCESS` connecting route is flagged `metapath_priority="Medium"`
(available as a softer, benefit-weighting signal, e.g. folded into
`Benefit_j` via an extra `w_metapath` term) rather than an unconditional hard
trigger, so that a single long highway is not entirely hard-constrained end
to end. Practically: join a repair lot's chainage/geometry against
`segment_hard_constraint_flags.geojson` by spatial overlap to pull in
`is_metapath_hard` / `metapath_priority` / `metapath_pairs` per lot.

## Known limitations / further considerations

* OSM tagging of warehouses in this part of Haryana is sparse (17 raw
  `building=warehouse` features); DBSCAN + 6 km seed-snapping was used to
  still recover the 3 named logistics hubs, but smaller/newer logistics
  parks not yet mapped in OSM will not appear as separate clusters.
  Un-named auto-clusters are mostly single OSM polygons (`n_members=1-2`)
  and are excluded from the hard-constraint export by design.
* Because the bbox was deliberately sized to span the named places along
  NH48, a `H_CHAIN_ACCESS` route between two named clusters can cover a
  large fraction of that corridor's extent inside the bbox; the
  High/Medium split above (near-cluster vs. full-route) is the mitigation
  used here. For a sparser/stricter hard-constraint set, shrink
  `CHAIN_DISTANCE_THRESHOLD_KM` or flag at a finer chainage-bin resolution
  rather than per OSM way.
* `NEIGHBOR_RADII_KM`, `CHAIN_DISTANCE_THRESHOLD_KM`, `DBSCAN_EPS_M`, and the
  priority thresholds are all centralized in `src/config.py` for easy
  sensitivity testing.

## License

MIT License — see [LICENSE](LICENSE).

