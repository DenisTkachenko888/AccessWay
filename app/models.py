"""
Data contracts for AccessWay MVP 0.

Key architectural decision: the central schema is Evidence, not AccessibilitySignal.
Evidence represents one sourced, timestamped, confidence-rated data point.
This allows future sources (user confirmations, city datasets, CV) to feed
the same scoring pipeline without restructuring.

Pipeline:
    Any source → Evidence → ScoringOutput → PlaceReport
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for model defaults."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Evidence — the core building block
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """A single piece of accessibility-relevant data from one source."""

    fact_key: str = Field(
        description="Machine-readable fact identifier, e.g. 'wheelchair_access', 'entrance_step_free'"
    )
    source_type: str = Field(
        pattern="^(osm|user_confirmation|official|visual_analysis|derived)$",
        description="Origin of this evidence",
    )
    source_id: Optional[str] = Field(
        default=None,
        description="Identifier within the source (OSM element ID, image URL, etc.)",
    )
    source_detail: str = Field(
        description="Human-readable source description, e.g. 'OSM tag wheelchair=yes'"
    )
    raw_value: Optional[str] = Field(
        default=None,
        description="Literal value from the source before interpretation",
    )
    interpreted_value: str = Field(
        pattern="^(yes|no|limited|unknown)$",
        description="Normalized interpretation of raw_value",
    )
    present: bool = Field(
        description="False = this fact key was checked and found absent in the source"
    )
    confidence: str = Field(
        pattern="^(high|medium|low|none)$",
        description="Confidence in this evidence item. 'none' = tag absent entirely",
    )
    collected_at: datetime = Field(
        default_factory=utc_now,
        description="When AccessWay fetched this data",
    )
    observed_at: Optional[datetime] = Field(
        default=None,
        description="When the source claims this was true (OSM last-edit date, etc.)",
    )
    freshness_days: Optional[int] = Field(
        default=None,
        description="Age of source data in days at collection time. None = unknown.",
    )


# ---------------------------------------------------------------------------
# Data provenance — one record per collection run
# ---------------------------------------------------------------------------

class DataProvenance(BaseModel):
    """Metadata about one data collection run for a place."""

    source_type: str = Field(description="Primary source used, e.g. 'osm'")
    collected_at: datetime = Field(default_factory=utc_now)
    osm_last_edit: Optional[datetime] = Field(
        default=None,
        description="Most recent OSM edit timestamp for this element",
    )
    freshness_label: str = Field(
        pattern="^(fresh|recent|aging|stale|very_stale|unknown)$",
        description="Human-readable freshness classification",
    )
    evidence_checked: int = Field(description="Number of fact keys attempted")
    evidence_present: int = Field(description="Number of fact keys with actual data found")
    evidence_absent: int = Field(description="Number of fact keys where data was missing")


# ---------------------------------------------------------------------------
# Scoring output
# ---------------------------------------------------------------------------

class Reason(BaseModel):
    """A single reason item in the scoring output, traceable to evidence."""

    text: str
    fact_key: str = Field(description="Which evidence item this reason comes from")
    source_type: str


class ScoringOutput(BaseModel):
    """The engine's verdict on a place, derived from Evidence."""

    verdict: str = Field(
        pattern="^(likely_accessible|partially_accessible|risky|unlikely_accessible|unknown)$"
    )
    confidence: str = Field(pattern="^(high|medium|low|none)$")
    decision_advice: str = Field(
        pattern="^(good_option|verify_first|choose_alternative|do_not_rely)$",
        description="Actionable advice for the user",
    )
    reasons: List[Reason]
    risks: List[str]
    unknowns: List[str] = Field(
        description="fact_keys that were checked but found absent — shown explicitly to user"
    )
    evidence_count: int = Field(description="Total evidence items that informed this verdict")


# ---------------------------------------------------------------------------
# Place report — the final output
# ---------------------------------------------------------------------------

class PlaceReport(BaseModel):
    """Complete accessibility report for one place."""

    name: str
    category: str
    lat: float
    lon: float
    osm_id: int
    osm_type: str = Field(default="node", description="OSM element type: node, way, or relation")
    evidence: List[Evidence]
    scoring: ScoringOutput
    provenance: DataProvenance


# ---------------------------------------------------------------------------
# Search contracts
# ---------------------------------------------------------------------------

class GeocodedLocation(BaseModel):
    """Normalized location returned by the geocoder."""

    lat: float
    lon: float
    display_name: str
    osm_type: Optional[str] = None
    osm_id: Optional[int] = None


class AddressSearchResponse(BaseModel):
    """Typed response for address-based place search."""

    query: str
    location: GeocodedLocation
    results: List[PlaceReport]
