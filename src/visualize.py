"""Step 4 (visualization): plot the Gurgaon highway network together with the
FactoryCluster / LogisticsCluster nodes and their Highway-neighbor metapath
links, using GeoPandas + OSM data.

Produces:
  outputs/figures/gurgaon_highway_metapath_map.png   (static overview map)
  outputs/figures/gurgaon_highway_metapath_map.html  (interactive folium map)
"""

from __future__ import annotations

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import networkx as nx
import osmnx as ox
import pandas as pd
from matplotlib.lines import Line2D
from shapely.geometry import LineString

from . import config, label_export, metapath
from .highway_segments import build_segments_and_corridors, load_highway_graph

HIGHWAY_COLORS = {
    "motorway": "#b30000",
    "motorway_link": "#b30000",
    "trunk": "#e6550d",
    "trunk_link": "#e6550d",
    "primary": "#2171b5",
    "primary_link": "#2171b5",
}


def _edge_color(highway_value) -> str:
    if isinstance(highway_value, list):
        highway_value = highway_value[0]
    return HIGHWAY_COLORS.get(highway_value, "#999999")


def _path_geometry(graph_directed: nx.MultiDiGraph, path_nodes: list) -> LineString | None:
    coords = []
    for u, v in zip(path_nodes[:-1], path_nodes[1:]):
        data = None
        if graph_directed.has_edge(u, v):
            data = graph_directed.get_edge_data(u, v)[0]
        elif graph_directed.has_edge(v, u):
            data = graph_directed.get_edge_data(v, u)[0]
        if data is None or "geometry" not in data:
            pu = graph_directed.nodes[u]
            pv = graph_directed.nodes[v]
            coords.extend([(pu["x"], pu["y"]), (pv["x"], pv["y"])])
        else:
            coords.extend(list(data["geometry"].coords))
    if len(coords) < 2:
        return None
    return LineString(coords)


def plot_static_map(segments, clusters, pair_table, out_path):
    fig, ax = plt.subplots(figsize=(13, 11))

    for hw_class, color in HIGHWAY_COLORS.items():
        subset = segments[segments["highway"].apply(
            lambda v: (v == hw_class) or (isinstance(v, list) and hw_class in v)
        )]
        if len(subset):
            subset.plot(ax=ax, color=color, linewidth=1.6, zorder=2)

    factories = clusters[clusters["node_type"] == "factory"]
    logistics = clusters[clusters["node_type"] == "logistics"]
    factories.plot(ax=ax, color="#d62728", marker="^", markersize=90, zorder=4, edgecolor="black")
    logistics.plot(ax=ax, color="#1f77b4", marker="s", markersize=90, zorder=4, edgecolor="black")

    # radius buffers (2/3/4/5 km) around named seed clusters only, to avoid clutter
    named_wgs = clusters[clusters["is_named_seed"]]
    named_metric = named_wgs.to_crs(config.CRS_METRIC)
    for r_km in config.NEIGHBOR_RADII_KM:
        buffers = named_metric.copy()
        buffers["geometry"] = buffers.geometry.buffer(r_km * 1000)
        buffers = buffers.to_crs(config.CRS_WGS84)
        buffers.boundary.plot(ax=ax, color="gray", linewidth=0.4, linestyle="--", alpha=0.6, zorder=1)

    for _, row in named_wgs.iterrows():
        ax.annotate(
            row["name"], (row.geometry.x, row.geometry.y), fontsize=8, fontweight="bold",
            xytext=(4, 4), textcoords="offset points", zorder=5,
        )

    # metapath connector lines: restrict to named-seed <-> named-seed pairs so
    # the headline (IMT Manesar, Udyog Vihar, Sohna <-> Bilaspur, Farrukhnagar,
    # Luhari) links stay legible; the full pair set (incl. auto clusters) is
    # still exported in full to the labels CSV.
    name_to_point = {row["name"]: row.geometry for _, row in clusters.iterrows()}
    named_names = set(clusters.loc[clusters["is_named_seed"], "name"])
    for _, row in pair_table.iterrows():
        if row["priority"] not in ("High", "Medium"):
            continue
        if row["factory_name"] not in named_names or row["logistics_name"] not in named_names:
            continue
        p1 = name_to_point.get(row["factory_name"])
        p2 = name_to_point.get(row["logistics_name"])
        if p1 is None or p2 is None:
            continue
        color = "#d62728" if row["priority"] == "High" else "#ff9f1c"
        ax.plot([p1.x, p2.x], [p1.y, p2.y], color=color, linewidth=1.6, linestyle=":", alpha=0.85, zorder=3)

    west, south, east, north = config.BBOX
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_title(
        "Gurgaon Highway-neighbor Metapath: Industrial Park <-> Logistics District\n"
        "(NH48-centred supply-chain belt; dashed circles = 2/3/4/5 km neighbor radii)",
        fontsize=12,
    )
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    legend_elems = [
        Line2D([0], [0], color=c, lw=2, label=hw) for hw, c in
        {"motorway": HIGHWAY_COLORS["motorway"], "trunk": HIGHWAY_COLORS["trunk"], "primary": HIGHWAY_COLORS["primary"]}.items()
    ] + [
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#d62728", markeredgecolor="black", markersize=10, label="FactoryCluster (Industrial Park)"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#1f77b4", markeredgecolor="black", markersize=10, label="LogisticsCluster (Logistics District)"),
        Line2D([0], [0], color="#d62728", lw=1.2, linestyle=":", label="Metapath link (High priority)"),
        Line2D([0], [0], color="#ff9f1c", lw=1.2, linestyle=":", label="Metapath link (Medium priority)"),
    ]
    ax.legend(handles=legend_elems, loc="lower left", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def plot_interactive_map(segments, clusters, pair_table, graph_directed, h_chain_rows, out_path):
    west, south, east, north = config.BBOX
    center = [(south + north) / 2, (west + east) / 2]
    m = folium.Map(location=center, zoom_start=11, tiles="OpenStreetMap")

    for _, row in segments.iterrows():
        color = _edge_color(row["highway"])
        folium.PolyLine(
            locations=[(lat, lon) for lon, lat in row.geometry.coords],
            color=color, weight=2, opacity=0.6,
            tooltip=f"{row['corridor_id']} ({row['highway']})",
        ).add_to(m)

    for _, row in clusters.iterrows():
        color = "red" if row["node_type"] == "factory" else "blue"
        icon = "industry" if row["node_type"] == "factory" else "warehouse"
        folium.Marker(
            location=(row.geometry.y, row.geometry.x),
            popup=f"{row['name']} ({row['node_type']}, n_members={row['n_members']})",
            tooltip=row["name"],
            icon=folium.Icon(color=color, icon=icon, prefix="fa"),
        ).add_to(m)
        if row["is_named_seed"]:
            for r_km in config.NEIGHBOR_RADII_KM:
                folium.Circle(
                    location=(row.geometry.y, row.geometry.x),
                    radius=r_km * 1000, color="gray", weight=0.7, fill=False, opacity=0.5,
                ).add_to(m)

    chain_lookup = {
        (r["factory_cluster_id"], r["logistics_cluster_id"]): r for r in h_chain_rows
    }
    name_to_id = {row["name"]: row["cluster_id"] for _, row in clusters.iterrows()}
    # restrict drawn connector lines to named-seed <-> named-seed pairs (same
    # rationale as the static map) so the interactive map highlights the
    # headline Industrial Park <-> Logistics District links clearly.
    named_names = set(clusters.loc[clusters["is_named_seed"], "name"])
    for _, row in pair_table.iterrows():
        if row["priority"] not in ("High", "Medium"):
            continue
        if row["factory_name"] not in named_names or row["logistics_name"] not in named_names:
            continue
        key = (name_to_id.get(row["factory_name"]), name_to_id.get(row["logistics_name"]))
        chain = chain_lookup.get(key)
        color = "red" if row["priority"] == "High" else "orange"
        popup = f"{row['factory_name']} <-> {row['logistics_name']}<br>{row['metapath_labels']}<br>priority={row['priority']}"
        if chain is not None:
            geom = _path_geometry(graph_directed, chain["path_node_ids"])
            if geom is not None:
                folium.PolyLine(
                    locations=[(lat, lon) for lon, lat in geom.coords],
                    color=color, weight=3, opacity=0.8, popup=popup,
                ).add_to(m)
                continue
        # fallback: straight line between centroids
        f_pt = clusters[clusters["name"] == row["factory_name"]].geometry.iloc[0]
        l_pt = clusters[clusters["name"] == row["logistics_name"]].geometry.iloc[0]
        folium.PolyLine(
            locations=[(f_pt.y, f_pt.x), (l_pt.y, l_pt.x)],
            color=color, weight=2, opacity=0.6, dash_array="5,5", popup=popup,
        ).add_to(m)

    m.save(out_path)


def run_visualization():
    clusters = gpd.read_file(config.DATA_PROCESSED_DIR / "clusters.geojson")
    segments, _, _ = build_segments_and_corridors()
    graph_directed = load_highway_graph()
    results = metapath.run_all()
    pair_table = label_export.build_pair_label_table(results)

    png_path = config.FIGURES_DIR / "gurgaon_highway_metapath_map.png"
    html_path = config.FIGURES_DIR / "gurgaon_highway_metapath_map.html"
    plot_static_map(segments, clusters, pair_table, png_path)
    plot_interactive_map(segments, clusters, pair_table, graph_directed, results["h_chain"], html_path)
    return png_path, html_path


if __name__ == "__main__":
    png_path, html_path = run_visualization()
    print(f"saved -> {png_path}")
    print(f"saved -> {html_path}")
