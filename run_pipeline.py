"""End-to-end entry point for the Highway-neighbor Metapath Labeling MVP.

Run with:  .venv-himet\\Scripts\\python.exe run_pipeline.py [--force-refetch]

Steps
-----
1. osm_fetch     : fetch/cache highway network + industrial/warehouse OSM layers
2. clustering    : DBSCAN cluster into FactoryCluster / LogisticsCluster nodes
3. metapath      : NEAR_HIGHWAY, H_NEIGHBOR(r), H_CHAIN_ACCESS(distance)
4. label_export  : Factory x Logistics pair labels + HighwaySegment hard-constraint flags
5. visualize     : static PNG + interactive HTML maps
"""

from __future__ import annotations

import argparse

from src import config, clustering, label_export, osm_fetch, visualize


def main(force_refetch: bool = False):
    print("=" * 70)
    print("Step 1/5: fetching OSM data (highway network, industrial, warehouse)")
    print("=" * 70)
    graph, industrial, warehouses = osm_fetch.fetch_all(force=force_refetch)
    print(f"  highway graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")
    print(f"  industrial polygons: {len(industrial)} | warehouse features: {len(warehouses)}")

    print("\n" + "=" * 70)
    print("Step 2/5: clustering into FactoryCluster / LogisticsCluster nodes")
    print("=" * 70)
    clusters = clustering.build_clusters()
    clusters.to_file(config.DATA_PROCESSED_DIR / "clusters.geojson", driver="GeoJSON")
    named = clusters[clusters["is_named_seed"]]
    print(f"  {len(clusters)} clusters total ({len(named)} matched to named seed places)")
    for _, row in named.iterrows():
        print(f"    - {row['name']:30s} [{row['node_type']}]  n_members={row['n_members']}")

    print("\n" + "=" * 70)
    print("Step 3-4/5: metapath labeling + export")
    print("=" * 70)
    pair_table, segment_table = label_export.run_export()
    print(f"  Factory x Logistics pairs labeled: {len(pair_table)}")
    print(f"  Priority breakdown:\n{pair_table['priority'].value_counts().to_string()}")
    print(f"  HighwaySegments flagged hard (High priority): {segment_table['is_metapath_hard'].sum()} / {len(segment_table)}")

    print("\n  Top named Industrial Park <-> Logistics District pairs:")
    named_names = set(named["name"])
    headline = pair_table[
        pair_table["factory_name"].isin(named_names) & pair_table["logistics_name"].isin(named_names)
    ]
    for _, row in headline.iterrows():
        print(
            f"    {row['factory_name']:25s} <-> {row['logistics_name']:28s} "
            f"road_dist={row['road_distance_km']:.1f}km  priority={row['priority']}  "
            f"labels=[{row['metapath_labels']}]"
        )

    print("\n" + "=" * 70)
    print("Step 5/5: visualization (static + interactive maps)")
    print("=" * 70)
    png_path, html_path = visualize.run_visualization()
    print(f"  saved -> {png_path}")
    print(f"  saved -> {html_path}")

    print("\nDone. Labels in:", config.LABELS_DIR)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-refetch", action="store_true", help="bypass OSM cache and re-download")
    args = parser.parse_args()
    main(force_refetch=args.force_refetch)
