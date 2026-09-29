"""
External data collectors for AccessWay.

Responsibilities:
- fetch raw POI elements from OpenStreetMap through Overpass;
- geocode a human-readable address through Nominatim.

No accessibility scoring or product decisions belong here. Collectors return
raw/normalized source data and leave interpretation to the scoring engine.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
NOMINATIM_USER_AGENT = os.getenv(
    "ACCESSWAY_USER_AGENT",
    "AccessWay/0.1 (accessibility research MVP)",
)

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
    """Build an Overpass QL query for POIs around a coordinate."""
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
        element_blocks.append(
            f'  relation(around:{radius},{lat},{lon})["{key}"="{value}"];'
        )

    elements_str = "\n".join(element_blocks)

    return f"""[out:json][timeout:25];
(
{elements_str}
);
out center meta tags;
"""


def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse an OSM ISO-8601 timestamp."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _extract_centroid(element: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Extract usable coordinates from a node, way, or relation."""
    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon}

    if element.get("type") in ("way", "relation"):
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")
        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon}

    return None


async def geocode_address(query: str) -> Optional[Dict[str, Any]]:
    """
    Resolve a human-readable address/place name to one coordinate.

    The function deliberately returns a tiny neutral contract so the API layer
    does not depend on the raw Nominatim response shape.
    """
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
    }
    headers = {
        "User-Agent": NOMINATIM_USER_AGENT,
        "Accept-Language": "ru,en;q=0.8",
    }

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, headers=headers) as session:
        async with session.get(NOMINATIM_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

    if not data:
        return None

    first = data[0]
    try:
        lat = float(first["lat"])
        lon = float(first["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "lat": lat,
        "lon": lon,
        "display_name": first.get("display_name", query),
        "osm_type": first.get("osm_type"),
        "osm_id": first.get("osm_id"),
    }


async def fetch_raw_places(
    lat: float,
    lon: float,
    radius: int,
    category: str,
) -> List[Dict[str, Any]]:
    """Fetch and normalize raw OSM POIs around a coordinate."""
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
            continue

        tags = element.get("tags", {})
        timestamp = _parse_timestamp(element.get("timestamp"))

        name = (
            tags.get("name:ru")
            or tags.get("name")
            or tags.get("name:en")
            or f"Unnamed {category}"
        )

        results.append({
            "id": element.get("id"),
            "osm_type": element.get("type", "node"),
            "lat": coords["lat"],
            "lon": coords["lon"],
            "tags": tags,
            "timestamp": timestamp,
            "name": name,
            "category": category,
        })

    return results


async def fetch_single_place(osm_type: str, osm_id: int) -> Optional[Dict[str, Any]]:
    """Fetch one OSM element by ID and type."""
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
        tags.get("name:ru")
        or tags.get("name")
        or tags.get("name:en")
        or f"OSM {osm_type} {osm_id}"
    )

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
