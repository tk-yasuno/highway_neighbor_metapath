"""Step 1: Fetch OSM geometries for a region's bbox (see ``src/regions.py``).

Pulls three raw layers via OSMnx/Overpass and caches them to the region's
``data_raw_dir`` as GeoJSON / GraphML so repeated pipeline runs do not re-hit
the Overpass API:

* highway network   -> motorway/trunk/primary (+ links), as a routable graph
* industrial parks   -> landuse=industrial polygons
* warehouses         -> building=warehouse / industrial=warehouse features

The public ``overpass-api.de`` endpoint resolves to multiple IPv4/IPv6
addresses; on networks where IPv6 connectivity is present but slow/blackholed
(common on dual-stack Windows networks), ``requests``/``urllib3`` can spend
most of its connect-timeout budget retrying unreachable IPv6 candidates
before falling back to a working IPv4 address, making requests appear to
hang far longer than the configured timeout. Environment variables let this
(and related connectivity quirks) be worked around without changing the
documented default behavior:

* ``HIMET_FORCE_IPV4`` (default on; set to ``"0"`` to disable) -- filter
  ``socket.getaddrinfo`` results to IPv4 only for this process, avoiding the
  slow IPv6-then-fallback path described above.
* ``HIMET_OVERPASS_URL`` -- override ``ox.settings.overpass_url`` (e.g. a
  community mirror such as ``https://overpass.kumi.systems/api``).
* ``HIMET_OSM_TIMEOUT``  -- override ``ox.settings.requests_timeout`` in
  seconds (default 180; larger regions with dense OSM tagging may need more).
* ``HIMET_PIN_DNS``      -- comma-separated ``host=ip`` pairs (e.g.
  ``overpass-api.de=162.55.144.139``) to pin a hostname to a single IP for
  this process only, bypassing DNS round-robin to an unreachable address
  without requiring a hosts-file edit or admin privileges. Takes precedence
  over ``HIMET_FORCE_IPV4`` for any pinned host.
* ``HIMET_OSM_RETRIES``  -- number of attempts per layer before giving up
  (default 3). Public Overpass mirrors intermittently return transient
  connection timeouts / 502 Bad Gateway under load; retrying with a short
  backoff resolves most of these without manual intervention.
* ``HIMET_OSM_RETRY_DELAY`` -- seconds to wait between retry attempts
  (default 20).
"""

from __future__ import annotations

import os
import socket
import time

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd

from . import config, regions
from .regions import RegionConfig

_pinned_hosts: dict[str, str] = {}
if os.environ.get("HIMET_PIN_DNS"):
    for _pair in os.environ["HIMET_PIN_DNS"].split(","):
        _host, _ip = _pair.split("=")
        _pinned_hosts[_host.strip()] = _ip.strip()

_force_ipv4 = os.environ.get("HIMET_FORCE_IPV4", "1") not in ("0", "false", "False")
if _pinned_hosts or _force_ipv4:
    _orig_getaddrinfo = socket.getaddrinfo

    def _patched_getaddrinfo(host, *args, **kwargs):
        if host in _pinned_hosts:
            return _orig_getaddrinfo(_pinned_hosts[host], *args, **kwargs)
        results = _orig_getaddrinfo(host, *args, **kwargs)
        if _force_ipv4:
            v4_only = [r for r in results if r[0] == socket.AF_INET]
            return v4_only or results
        return results

    socket.getaddrinfo = _patched_getaddrinfo

ox.settings.use_cache = True
ox.settings.cache_folder = str(config.DATA_RAW_DIR / "osmnx_cache")
if os.environ.get("HIMET_OVERPASS_URL"):
    ox.settings.overpass_url = os.environ["HIMET_OVERPASS_URL"]
    # third-party mirrors don't reliably expose the overpass-api.de-style
    # rate-limit "status" endpoint; skip that extra round trip by default.
    ox.settings.overpass_rate_limit = os.environ.get("HIMET_OVERPASS_RATE_LIMIT", "0") in ("1", "true", "True")
ox.settings.requests_timeout = int(os.environ.get("HIMET_OSM_TIMEOUT", "180"))
ox.settings.log_console = False

_OSM_RETRIES = int(os.environ.get("HIMET_OSM_RETRIES", "3"))
_OSM_RETRY_DELAY = float(os.environ.get("HIMET_OSM_RETRY_DELAY", "20"))


def _with_retries(label: str, func, *args, **kwargs):
    """Call ``func(*args, **kwargs)``, retrying up to ``_OSM_RETRIES`` times
    on any exception (connection timeouts, 502 Bad Gateway, etc. are common
    and transient on public Overpass mirrors under load)."""
    last_exc: Exception | None = None
    for attempt in range(1, _OSM_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - intentionally broad: network/server errors vary
            last_exc = exc
            print(f"  [osm_fetch] {label} attempt {attempt}/{_OSM_RETRIES} failed: {exc!r}")
            if attempt < _OSM_RETRIES:
                time.sleep(_OSM_RETRY_DELAY)
    assert last_exc is not None
    raise last_exc


def fetch_highway_graph(region: RegionConfig, force: bool = False) -> nx.MultiDiGraph:
    """Fetch the motorway/trunk/primary road network as a routable graph."""
    regions.ensure_dirs(region)
    cache_path = region.data_raw_dir / "highway_graph.graphml"
    if cache_path.exists() and not force:
        return ox.load_graphml(cache_path)

    graph = _with_retries(
        "highway graph",
        ox.graph_from_bbox,
        bbox=region.bbox,
        custom_filter=config.HIGHWAY_CUSTOM_FILTER,
        simplify=True,
        retain_all=True,
    )
    ox.save_graphml(graph, cache_path)
    return graph


def fetch_industrial_polygons(region: RegionConfig, force: bool = False) -> gpd.GeoDataFrame:
    """Fetch landuse=industrial polygons (Industrial Park candidates)."""
    regions.ensure_dirs(region)
    cache_path = region.data_raw_dir / "industrial_polygons.geojson"
    if cache_path.exists() and not force:
        return gpd.read_file(cache_path)

    gdf = _with_retries("industrial polygons", ox.features_from_bbox, bbox=region.bbox, tags=config.INDUSTRIAL_TAGS)
    gdf = gdf.reset_index()
    gdf = gdf[["element", "id", "geometry"] + _extra_cols(gdf, ["name", "landuse"])]
    gdf.to_file(cache_path, driver="GeoJSON")
    return gdf


def fetch_warehouse_features(region: RegionConfig, force: bool = False) -> gpd.GeoDataFrame:
    """Fetch building=warehouse / industrial=warehouse features (Logistics District candidates)."""
    regions.ensure_dirs(region)
    cache_path = region.data_raw_dir / "warehouse_features.geojson"
    if cache_path.exists() and not force:
        return gpd.read_file(cache_path)

    frames = []
    for tags in config.WAREHOUSE_TAGS_LIST:
        try:
            gdf = _with_retries(f"warehouse features {tags}", ox.features_from_bbox, bbox=region.bbox, tags=tags)
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


def fetch_all(region: RegionConfig, force: bool = False):
    """Fetch (and cache) all three raw OSM layers; returns (graph, industrial_gdf, warehouse_gdf)."""
    graph = fetch_highway_graph(region, force=force)
    industrial = fetch_industrial_polygons(region, force=force)
    warehouses = fetch_warehouse_features(region, force=force)
    return graph, industrial, warehouses


if __name__ == "__main__":
    _region = regions.get_region("gurgaon")
    g, ind, wh = fetch_all(_region)
    print(f"highway graph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
    print(f"industrial polygons: {len(ind)}")
    print(f"warehouse features: {len(wh)}")
