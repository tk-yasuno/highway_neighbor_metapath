"""Step 1: Fetch OSM geometries for the Gurgaon bbox.

Pulls three raw layers via OSMnx/Overpass and caches them to ``data/raw`` as
GeoJSON / GraphML so repeated pipeline runs do not re-hit the Overpass API:

* highway network   -> motorway/trunk/primary (+ links), as a routable graph
* industrial parks   -> landuse=industrial polygons
* warehouses         -> building=warehouse / industrial=warehouse features
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd

from . import config

ox.settings.use_cache = True
ox.settings.cache_folder = str(config.DATA_RAW_DIR / "osmnx_cache")
ox.settings.log_console = False


def _bbox_for_osmnx():
    """osmnx>=2.0 expects bbox as (left, bottom, right, top) = (west, south, east, north)."""
    return config.BBOX


def fetch_highway_graph(force: bool = False) -> nx.MultiDiGraph:
    """Fetch the motorway/trunk/primary road network as a routable graph."""
    cache_path = config.DATA_RAW_DIR / "highway_graph.graphml"
    if cache_path.exists() and not force:
        return ox.load_graphml(cache_path)

    graph = ox.graph_from_bbox(
        bbox=_bbox_for_osmnx(),
        custom_filter=config.HIGHWAY_CUSTOM_FILTER,
        simplify=True,
        retain_all=True,
    )
    ox.save_graphml(graph, cache_path)
    return graph


def fetch_industrial_polygons(force: bool = False) -> gpd.GeoDataFrame:
    """Fetch landuse=industrial polygons (Industrial Park candidates)."""
    cache_path = config.DATA_RAW_DIR / "industrial_polygons.geojson"
    if cache_path.exists() and not force:
        return gpd.read_file(cache_path)

    gdf = ox.features_from_bbox(bbox=_bbox_for_osmnx(), tags=config.INDUSTRIAL_TAGS)
    gdf = gdf.reset_index()
    gdf = gdf[["element", "id", "geometry"] + _extra_cols(gdf, ["name", "landuse"])]
    gdf.to_file(cache_path, driver="GeoJSON")
    return gdf


def fetch_warehouse_features(force: bool = False) -> gpd.GeoDataFrame:
    """Fetch building=warehouse / industrial=warehouse features (Logistics District candidates)."""
    cache_path = config.DATA_RAW_DIR / "warehouse_features.geojson"
    if cache_path.exists() and not force:
        return gpd.read_file(cache_path)

    frames = []
    for tags in config.WAREHOUSE_TAGS_LIST:
        try:
            gdf = ox.features_from_bbox(bbox=_bbox_for_osmnx(), tags=tags)
        except Exception:
            continue
        if len(gdf):
            frames.append(gdf.reset_index())
    if not frames:
        combined = gpd.GeoDataFrame(columns=["element", "id", "geometry"], geometry="geometry", crs=config.CRS_WGS84)
    else:
        combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
        combined = combined.drop_duplicates(subset=["element", "id"])
        keep_cols = ["element", "id", "geometry"] + _extra_cols(combined, ["name", "building", "industrial"])
        combined = combined[keep_cols]
    combined.to_file(cache_path, driver="GeoJSON")
    return combined


def _extra_cols(gdf: gpd.GeoDataFrame, cols: list[str]) -> list[str]:
    return [c for c in cols if c in gdf.columns]


def fetch_all(force: bool = False):
    """Fetch (and cache) all three raw OSM layers; returns (graph, industrial_gdf, warehouse_gdf)."""
    graph = fetch_highway_graph(force=force)
    industrial = fetch_industrial_polygons(force=force)
    warehouses = fetch_warehouse_features(force=force)
    return graph, industrial, warehouses


if __name__ == "__main__":
    g, ind, wh = fetch_all()
    print(f"highway graph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
    print(f"industrial polygons: {len(ind)}")
    print(f"warehouse features: {len(wh)}")
