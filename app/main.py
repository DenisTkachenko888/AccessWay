"""
AccessWay FastAPI application — MVP 0.2.

Request pipeline:
    collector -> Evidence extraction -> scoring -> PlaceReport -> product response

The HTTP layer orchestrates data collection and presentation only. Accessibility
logic remains in app.engine so it stays deterministic and independently testable.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.collector import fetch_raw_places, fetch_single_place, geocode_address
from app.engine import build_provenance, extract_evidence, score
from app.models import AddressSearchResponse, PlaceReport

API_VERSION = "0.2.0"
CITY = os.getenv("ACCESSWAY_CITY", "Moscow")
DEBUG = os.getenv("ACCESSWAY_DEBUG", "false").lower() == "true"

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AccessWay API",
    description=(
        "Evidence-based accessibility intelligence for urban places. "
        "The API explains what is known, what is uncertain, and how confident "
        "the current accessibility assessment is."
    ),
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("ACCESSWAY_CORS_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _build_report(raw: dict) -> PlaceReport:
    evidence = extract_evidence(
        tags=raw["tags"],
        osm_last_edit=raw.get("timestamp"),
        source_id=str(raw["id"]),
    )
    scoring = score(evidence, category=raw.get("category", "unknown"))
    provenance = build_provenance(evidence, osm_last_edit=raw.get("timestamp"))

    return PlaceReport(
        name=raw["name"],
        category=raw.get("category", "unknown"),
        lat=raw["lat"],
        lon=raw["lon"],
        osm_id=raw["id"],
        osm_type=raw.get("osm_type", "node"),
        evidence=evidence,
        scoring=scoring,
        provenance=provenance,
    )


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres using the haversine formula."""
    earth_radius_m = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * earth_radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _rank_reports(reports: List[PlaceReport], lat: float, lon: float) -> List[PlaceReport]:
    """Rank by accessibility assessment, confidence, then distance."""
    verdict_order = {
        "likely_accessible": 0,
        "partially_accessible": 1,
        "risky": 2,
        "unknown": 3,
        "unlikely_accessible": 4,
    }
    confidence_order = {"high": 0, "medium": 1, "low": 2, "none": 3}

    return sorted(
        reports,
        key=lambda report: (
            verdict_order.get(report.scoring.verdict, 99),
            confidence_order.get(report.scoring.confidence, 99),
            _distance_m(lat, lon, report.lat, report.lon),
        ),
    )


async def _search_reports(
    lat: float,
    lon: float,
    radius_m: int,
    category: str,
    limit: int,
) -> List[PlaceReport]:
    try:
        raw_places = await fetch_raw_places(lat, lon, radius_m, category)
    except Exception as exc:
        logger.exception("Overpass fetch failed")
        raise HTTPException(
            status_code=502,
            detail="Не удалось загрузить данные о местах. Попробуйте ещё раз чуть позже.",
        ) from exc

    reports = [_build_report(raw) for raw in raw_places]
    return _rank_reports(reports, lat, lon)[:limit]


@app.get("/api/v1/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "version": API_VERSION,
        "city": CITY,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/search/nearby", response_model=List[PlaceReport], tags=["search"])
async def search_nearby(
    lat: float = Query(..., description="Latitude", ge=-90, le=90),
    lon: float = Query(..., description="Longitude", ge=-180, le=180),
    radius_m: int = Query(500, description="Search radius in metres", ge=50, le=3000),
    category: str = Query("pharmacy", description="Place category"),
    limit: int = Query(20, description="Maximum results", ge=1, le=50),
):
    """Find nearby places and return explainable accessibility reports."""
    return await _search_reports(lat, lon, radius_m, category, limit)


@app.get(
    "/api/v1/search/address",
    response_model=AddressSearchResponse,
    tags=["search"],
)
async def search_by_address(
    query: str = Query(..., min_length=2, description="Address or place name"),
    radius_m: int = Query(700, description="Search radius in metres", ge=50, le=3000),
    category: str = Query("pharmacy", description="Place category"),
    limit: int = Query(20, ge=1, le=50),
):
    """Geocode an address/place name and search for accessible POIs around it."""
    try:
        location = await geocode_address(query)
    except Exception as exc:
        logger.exception("Nominatim geocoding failed")
        raise HTTPException(
            status_code=502,
            detail="Поиск адреса временно недоступен. Попробуйте позже или используйте геолокацию.",
        ) from exc

    if location is None:
        raise HTTPException(status_code=404, detail="Адрес или место не найдено.")

    reports = await _search_reports(
        location["lat"],
        location["lon"],
        radius_m,
        category,
        limit,
    )

    return AddressSearchResponse(
        query=query,
        location=location,
        results=reports,
    )


@app.get("/api/v1/places/osm/{osm_type}/{osm_id}", response_model=PlaceReport, tags=["places"])
async def get_place_by_osm_id(osm_type: str, osm_id: int):
    if osm_type not in ("node", "way", "relation"):
        raise HTTPException(status_code=400, detail="osm_type must be node, way, or relation")

    try:
        raw = await fetch_single_place(osm_type, osm_id)
    except Exception as exc:
        logger.exception("Overpass single-element fetch failed")
        raise HTTPException(status_code=502, detail="Не удалось загрузить данные OpenStreetMap.") from exc

    if raw is None:
        raise HTTPException(status_code=404, detail=f"OSM {osm_type}/{osm_id} not found.")

    return _build_report(raw)


@app.get("/api/v1/places/osm/{osm_type}/{osm_id}/evidence", tags=["debug"])
async def get_place_evidence(osm_type: str, osm_id: int):
    if osm_type not in ("node", "way", "relation"):
        raise HTTPException(status_code=400, detail="osm_type must be node, way, or relation")

    try:
        raw = await fetch_single_place(osm_type, osm_id)
    except Exception as exc:
        logger.exception("Overpass evidence fetch failed")
        raise HTTPException(status_code=502, detail="Не удалось загрузить данные OpenStreetMap.") from exc

    if raw is None:
        raise HTTPException(status_code=404, detail=f"OSM {osm_type}/{osm_id} not found.")

    evidence = extract_evidence(
        tags=raw["tags"],
        osm_last_edit=raw.get("timestamp"),
        source_id=str(raw["id"]),
    )

    return {
        "osm_id": osm_id,
        "osm_type": osm_type,
        "name": raw["name"],
        "raw_tags": raw["tags"],
        "timestamp": raw.get("timestamp"),
        "evidence": [item.model_dump() for item in evidence],
    }
