import aiohttp
from typing import List, Dict, Any

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Маппинг категорий на правильные ключи OSM
CATEGORY_TAGS = {
    "pharmacy": "amenity",
    "cafe": "amenity",
    "restaurant": "amenity",
    "hospital": "amenity",
    "museum": "tourism",  # Вот наш ключ для музеев!
    "supermarket": "shop"
}

async def fetch_raw_places(lat: float, lon: float, radius: int, category: str) -> List[Dict[str, Any]]:
    # Достаем правильный ключ, по умолчанию используем amenity
    osm_key = CATEGORY_TAGS.get(category, "amenity")

    query = f"""[out:json][timeout:25];
    (
      node(around:{radius},{lat},{lon})["{osm_key}"="{category}"];
      way(around:{radius},{lat},{lon})["{osm_key}"="{category}"];
    );
    out center tags;
    """
    
    async with aiohttp.ClientSession() as session:
        async with session.post(OVERPASS_URL, data={"data": query}) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("elements", [])