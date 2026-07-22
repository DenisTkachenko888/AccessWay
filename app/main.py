"""
AccessWay FastAPI application — MVP 0

The core pipeline for every request:
    1. collector.fetch_raw_places()  → raw OSM elements with metadata
    2. engine.extract_evidence()     → Evidence list (sourced, timestamped)
    3. engine.score()                → ScoringOutput (verdict + confidence + reasons)
    4. PlaceReport assembled and returned

No database is used in MVP 0 for the scoring pipeline.
The DB layer (db_models, database) exists for the spatial search endpoint
and will be the persistence layer in MVP 1.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.collector import fetch_raw_places, fetch_single_place
from app.engine import build_provenance, extract_evidence, score
from app.models import PlaceReport

# ---------------------------------------------------------------------------
# Configuration from environment (never hardcode credentials)
# ---------------------------------------------------------------------------

API_VERSION = "0.1.0"
CITY = os.getenv("ACCESSWAY_CITY", "Moscow")
DEBUG = os.getenv("ACCESSWAY_DEBUG", "false").lower() == "true"

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AccessWay API",
    description=(
        "AI-powered accessibility intelligence for urban places. "
        "MVP 0: OSM data collection → signal extraction → confidence-scored place reports."
    ),
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — required for debug.html (file://) and future web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten to specific origins in production
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Internal pipeline helper
# ---------------------------------------------------------------------------

def _build_report(raw: dict) -> PlaceReport:
    """
    Given a raw element dict from the collector, run the full engine pipeline
    and return a PlaceReport.
    """
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/health", tags=["system"])
async def health():
    """Service health check. Always returns 200 if the app is running."""
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
    radius_m: int = Query(500, description="Search radius in metres", ge=50, le=2000),
    category: str = Query("pharmacy", description="Place category"),
    limit: int = Query(20, description="Maximum results to return", ge=1, le=50),
):
    """
    Find accessible places near a coordinate.

    Returns places sorted by accessibility confidence (highest first),
    with Unknown verdicts last. Each place includes full evidence list,
    scoring, and data provenance.
    """
    try:
        raw_places = await fetch_raw_places(lat, lon, radius_m, category)
    except Exception as exc:
        logger.error("Overpass fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch data from OpenStreetMap. Try again shortly.")

    if not raw_places:
        return []

    reports = [_build_report(r) for r in raw_places[:limit]]

    # Sort: known verdicts first (by confidence), unknown last
    _verdict_order = {
        "likely_accessible": 0,
        "partially_accessible": 1,
        "risky": 2,
        "unlikely_accessible": 3,
        "unknown": 4,
    }
    _confidence_order = {"high": 0, "medium": 1, "low": 2, "none": 3}

    reports.sort(key=lambda r: (
        _verdict_order.get(r.scoring.verdict, 99),
        _confidence_order.get(r.scoring.confidence, 99),
    ))

    return reports


@app.get("/api/v1/search/address", tags=["search"])
async def search_by_address(
    query: str = Query(..., description="Address or place name to search near"),
    radius_m: int = Query(500, description="Search radius in metres", ge=50, le=2000),
    category: str = Query("pharmacy", description="Place category"),
    limit: int = Query(20, ge=1, le=50),
):
    """
    Geocode an address and return nearby accessible places.

    Note: Nominatim geocoding is not yet wired in MVP 0.
    Returns a clear error message so the caller knows to use /search/nearby instead.
    """
    # TODO MVP 1: wire Nominatim geocoder here
    raise HTTPException(
        status_code=501,
        detail={
            "message": "Address search is not yet available in MVP 0. Use /search/nearby with lat/lon.",
            "workaround": "/api/v1/search/nearby?lat=55.7558&lon=37.6173&category=pharmacy",
        }
    )


@app.get("/api/v1/places/osm/{osm_type}/{osm_id}", response_model=PlaceReport, tags=["places"])
async def get_place_by_osm_id(
    osm_type: str,
    osm_id: int,
):
    """
    Fetch and score a specific OSM element by its type and ID.

    osm_type must be one of: node, way, relation
    osm_id is the integer OSM element ID.

    Example: /api/v1/places/osm/way/123456789
    """
    if osm_type not in ("node", "way", "relation"):
        raise HTTPException(status_code=400, detail="osm_type must be 'node', 'way', or 'relation'")

    try:
        raw = await fetch_single_place(osm_type, osm_id)
    except Exception as exc:
        logger.error("Overpass single-element fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch data from OpenStreetMap.")

    if raw is None:
        raise HTTPException(status_code=404, detail=f"OSM {osm_type}/{osm_id} not found.")

    return _build_report(raw)


@app.get("/api/v1/places/osm/{osm_type}/{osm_id}/evidence", tags=["debug"])
async def get_place_evidence(
    osm_type: str,
    osm_id: int,
):
    """
    Return the raw evidence list for a specific OSM element without scoring.

    Useful for validating the signal extractor against known ground truth
    during MVP 0 development. Shows exactly which OSM tags were found,
    their interpreted values, and freshness.
    """
    if osm_type not in ("node", "way", "relation"):
        raise HTTPException(status_code=400, detail="osm_type must be 'node', 'way', or 'relation'")

    try:
        raw = await fetch_single_place(osm_type, osm_id)
    except Exception as exc:
        logger.error("Overpass fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch data from OpenStreetMap.")

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
        "evidence": [e.model_dump() for e in evidence],
    }