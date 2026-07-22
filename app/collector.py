"""
OSM data collector for AccessWay.

Responsibility: fetch raw OSM elements from Overpass API.
No business logic here — pure I/O.

Returns raw elements INCLUDING metadata (timestamp, version) so the
scoring engine can calculate data freshness.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Maps internal category names to their OSM key=value pairs.
# A category can have multiple OSM representations — all are fetched.
CATEGORY_TO_OSM: Dict[str, List[Dict[str, str]]] = {
    "pharmacy":       [{"key": "amenity", "value": "pharmacy"}],
    "cafe":           [{"key": "amenity", "value": "cafe"}],
    "restaurant":     [{"key": "amenity", "value": "restaurant"}],
    "hospital":       [{"key": "amenity", "value": "hospital"}],
    "clinic":         [{"key": "amenity", "value": "clinic"}],
    "museum":         [{"key": "tourism", "value": "museum"}],
    "supermarket":    [{"key": "shop", "value": "supermarket"}],
    "mall":           [{"key": "shop", "value": "mall"}, {"key": "amenity", "value": "marketplace"}],
    "park":           [{"key": "leisure", "value": "park"}],
    "library":        [{"key": "amenity", "value": "library"}],
    "metro_station":  [{"key": "station", "value": "subway"}, {"key": "railway", "value": "station"}],
    "bank":           [{"key": "amenity", "value": "bank"}],
    "post_office":    [{"key": "amenity", "value": "post_office"}],
    "government":     [{"key": "amenity", "value": "townhall"}, {"key": "office", "value": "government"}],
}


def _build_overpass_query(lat: float, lon: float, radius: int, category: str) -> str:
    """
    Build an Overpass QL query that:
    - Returns nodes and ways within the radius
    - Includes full OSM metadata (timestamp, version) via 'meta'
    - Returns all tags
    - Handles multiple OSM representations of one category
    """
    osm_filters = CATEGORY_TO_OSM.get(category, [{"key": "amenity", "value": category}])

    element_blocks = []
    for osm_filter in osm_filters:
        key = osm_filter["key"]
        value = osm_filter["value"]
        element_blocks.append(
            f'  node(around:{radius},{lat},{lon})["{key}"="{value}"];'
        )
        element_blocks.append(
            f'  way(around:{radius},{lat},{lon})["{key}"="{value}"];'
        )

    elements_str = "\n".join(element_blocks)

    # 'out center meta tags' returns:
    #   center — centroid for ways (lat/lon)
    #   meta   — timestamp, version, changeset (needed for freshness)
    #   tags   — all OSM tags
    return f"""[out:json][timeout:25];
(
{elements_str}
);
out center meta tags;
"""


def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse OSM timestamp string (ISO 8601) to datetime."""
    if not ts:
        return None
    try:
        # OSM timestamps look like "2023-11-14T10:22:05Z"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _extract_centroid(element: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Extract lat/lon from a node or way element."""
    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon}
    elif element.get("type") == "way":
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")
        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon}
    return None


async def fetch_raw_places(
    lat: float,
    lon: float,
    radius: int,
    category: str,
) -> List[Dict[str, Any]]:
    """
    Fetch raw OSM elements from Overpass for a given location and category.

    Returns a list of normalized element dicts, each containing:
        id:           OSM element ID
        osm_type:     'node' or 'way'
        lat, lon:     centroid coordinates
        tags:         dict of all OSM tags
        timestamp:    datetime of last OSM edit (or None)
        name:         display name (from tags or fallback)
    """
    query = _build_overpass_query(lat, lon, radius, category)

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        async with session.post(OVERPASS_URL, data={"data": query}) as resp:
            resp.raise_for_status()
            data = await resp.json()

    elements = data.get("elements", [])
    results = []

    for element in elements:
        coords = _extract_centroid(element)
        if not coords:
            # Skip elements we can't place on a map
            continue

        tags = element.get("tags", {})
        timestamp = _parse_timestamp(element.get("timestamp"))

        name = (
            tags.get("name")
            or tags.get("name:ru")
            or tags.get("name:en")
            or f"Unnamed {category}"
        )

        results.append({
            "id": element.get("id"),
            "osm_type": element.get("type", "node"),
            "lat": coords["lat"],
            "lon": coords["lon"],
            "tags": tags,
            "timestamp": timestamp,  # OSM last-edit datetime
            "name": name,
            "category": category,
        })

    return results


async def fetch_single_place(osm_type: str, osm_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch a single OSM element by ID and type.
    Used by the /places/osm/{type}/{id} endpoint.
    """
    # Overpass query for a single element with full metadata
    type_char = {"node": "n", "way": "w", "relation": "r"}.get(osm_type, "n")
    query = f"""[out:json][timeout:15];
{type_char}({osm_id});
out center meta tags;
"""

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        async with session.post(OVERPASS_URL, data={"data": query}) as resp:
            resp.raise_for_status()
            data = await resp.json()

    elements = data.get("elements", [])
    if not elements:
        return None

    element = elements[0]
    coords = _extract_centroid(element)
    if not coords:
        return None

    tags = element.get("tags", {})
    timestamp = _parse_timestamp(element.get("timestamp"))
    name = (
        tags.get("name")
        or tags.get("name:ru")
        or tags.get("name:en")
        or f"OSM {osm_type} {osm_id}"
    )

    # Determine category from tags
    category = (
        tags.get("amenity")
        or tags.get("tourism")
        or tags.get("shop")
        or tags.get("leisure")
        or "unknown"
    )

    return {
        "id": osm_id,
        "osm_type": osm_type,
        "lat": coords["lat"],
        "lon": coords["lon"],
        "tags": tags,
        "timestamp": timestamp,
        "name": name,
        "category": category,
    }