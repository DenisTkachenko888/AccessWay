"""
AccessWay Scoring Engine — MVP 0

Two pure functions:
    extract_evidence(tags, osm_element_meta) -> List[Evidence]
    score(evidence, category, user_needs)    -> ScoringOutput

Pure means: no I/O, no database, no external calls.
Same inputs always produce same outputs.
Fully testable without mocking anything.

OSM tag reference used here:
    https://wiki.openstreetmap.org/wiki/Key:wheelchair
    https://wiki.openstreetmap.org/wiki/Key:entrance
    https://wiki.openstreetmap.org/wiki/Key:ramp
    https://wiki.openstreetmap.org/wiki/Key:kerb
    https://wiki.openstreetmap.org/wiki/Key:toilets:wheelchair
    https://wiki.openstreetmap.org/wiki/Key:surface
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.models import DataProvenance, Evidence, Reason, ScoringOutput


# ---------------------------------------------------------------------------
# OSM tag definitions
# Each entry maps a fact_key to one or more OSM tag keys that carry it.
# Order within each list = priority (first match wins).
# ---------------------------------------------------------------------------

_POSITIVE_VALUES = {"yes", "true", "1", "designated"}
_NEGATIVE_VALUES = {"no", "false", "0"}
_LIMITED_VALUES = {"limited"}

# Canonical OSM tags per fact.
# Source: https://wiki.openstreetmap.org/wiki/Key:wheelchair etc.
_OSM_TAG_MAP: Dict[str, List[str]] = {
    "wheelchair_access": ["wheelchair"],
    # entrance=level means step-free. step_count=0 also implies it.
    # Note: "entrance:step_free" is NOT a standard OSM tag.
    "entrance_step_free": ["entrance", "step_count"],
    # ramp:wheelchair=yes is the specific tag; ramp=yes alone is ambiguous
    "entrance_ramp": ["ramp:wheelchair", "ramp"],
    "kerb_type": ["kerb"],
    "step_count": ["step_count"],
    "accessible_toilet": ["toilets:wheelchair"],
    # surface affects usability but isn't a binary accessibility signal
    "surface_type": ["surface"],
    "door_automatic": ["automatic_door"],
    "elevator_present": ["elevator", "lift"],
    "parking_accessible": ["capacity:disabled"],
    "barrier_present": ["barrier"],
}

# Freshness thresholds in days
_FRESHNESS_THRESHOLDS = [
    (180, "fresh"),
    (365, "recent"),
    (730, "aging"),
    (1095, "stale"),
]


def _classify_freshness(days: Optional[int]) -> str:
    if days is None:
        return "unknown"
    for threshold, label in _FRESHNESS_THRESHOLDS:
        if days <= threshold:
            return label
    return "very_stale"


def _interpret_osm_value(fact_key: str, raw: Optional[str]) -> str:
    """Normalize a raw OSM tag value to yes/no/limited/unknown."""
    if raw is None:
        return "unknown"
    raw = raw.strip().lower()

    # Special case: entrance=level means step-free entrance
    if fact_key == "entrance_step_free" and raw == "level":
        return "yes"
    # step_count=0 means step-free
    if fact_key == "entrance_step_free" and raw == "0":
        return "yes"
    # Any step_count > 0 means NOT step-free
    if fact_key == "entrance_step_free":
        try:
            if int(raw) > 0:
                return "no"
        except (ValueError, TypeError):
            pass

    if raw in _POSITIVE_VALUES:
        return "yes"
    if raw in _NEGATIVE_VALUES:
        return "no"
    if raw in _LIMITED_VALUES:
        return "limited"
    return "unknown"


def _signal_confidence(interpreted: str, freshness_days: Optional[int]) -> str:
    """
    Determine confidence for a single evidence item.
    Rules:
    - If tag is absent: none
    - If interpreted value is unknown (tag present but unrecognized): low
    - Then apply freshness penalty
    """
    if interpreted == "unknown":
        return "none"

    freshness = _classify_freshness(freshness_days)

    if freshness in ("fresh", "recent"):
        return "high" if interpreted in ("yes", "no") else "medium"
    if freshness == "aging":
        return "medium" if interpreted in ("yes", "no") else "low"
    if freshness in ("stale", "very_stale"):
        return "low"
    # freshness unknown
    return "medium" if interpreted in ("yes", "no") else "low"


def extract_evidence(
    tags: Dict[str, str],
    osm_last_edit: Optional[datetime] = None,
    source_id: Optional[str] = None,
    collected_at: Optional[datetime] = None,
) -> List[Evidence]:
    """
    Extract Evidence from raw OSM tags.

    Always returns one Evidence item per fact_key — even for absent tags.
    Absent tags produce Evidence with present=False and confidence='none'.
    Absence is data, not nothing.

    Args:
        tags: Raw OSM tag dict from Overpass response
        osm_last_edit: Timestamp of the OSM element's last edit (from metadata)
        source_id: OSM element ID as string
        collected_at: When we fetched this data (defaults to now)
    """
    now = collected_at or datetime.now(timezone.utc)
    freshness_days: Optional[int] = None
    if osm_last_edit:
        # Make both timezone-aware for subtraction
        if osm_last_edit.tzinfo is None:
            osm_last_edit = osm_last_edit.replace(tzinfo=timezone.utc)
        delta = now - osm_last_edit
        freshness_days = max(0, delta.days)

    evidence_list: List[Evidence] = []

    for fact_key, tag_keys in _OSM_TAG_MAP.items():
        raw_value: Optional[str] = None
        matched_tag: Optional[str] = None

        # Try each candidate tag key in priority order
        for tag_key in tag_keys:
            if tag_key in tags:
                raw_value = tags[tag_key]
                matched_tag = tag_key
                break

        present = raw_value is not None
        interpreted = _interpret_osm_value(fact_key, raw_value) if present else "unknown"
        confidence = _signal_confidence(interpreted, freshness_days) if present else "none"

        source_detail = (
            f"OSM tag {matched_tag}={raw_value}"
            if present
            else f"Tag not found (checked: {', '.join(tag_keys)})"
        )

        evidence_list.append(Evidence(
            fact_key=fact_key,
            source_type="osm",
            source_id=source_id,
            source_detail=source_detail,
            raw_value=raw_value,
            interpreted_value=interpreted,
            present=present,
            confidence=confidence,
            collected_at=now,
            observed_at=osm_last_edit,
            freshness_days=freshness_days,
        ))

    return evidence_list


def build_provenance(
    evidence: List[Evidence],
    osm_last_edit: Optional[datetime] = None,
) -> DataProvenance:
    """Build a DataProvenance record from an evidence list."""
    present_count = sum(1 for e in evidence if e.present)
    absent_count = sum(1 for e in evidence if not e.present)
    freshness_days = next((e.freshness_days for e in evidence if e.freshness_days is not None), None)

    return DataProvenance(
        source_type="osm",
        osm_last_edit=osm_last_edit,
        freshness_label=_classify_freshness(freshness_days),
        evidence_checked=len(evidence),
        evidence_present=present_count,
        evidence_absent=absent_count,
    )


def score(
    evidence: List[Evidence],
    category: str = "unknown",
    user_needs: Optional[Dict] = None,
) -> ScoringOutput:
    """
    Produce a ScoringOutput from a list of Evidence items.

    Core rules:
    1. Zero present evidence → verdict: unknown, confidence: none
    2. wheelchair=no → verdict: unlikely_accessible, confidence: high
    3. wheelchair=yes + contradictory step evidence → verdict: risky
    4. wheelchair=yes + supporting evidence → confidence scales with density
    5. wheelchair=limited → partially_accessible
    6. No wheelchair tag but other signals present → low confidence unknown
    7. Freshness degrades confidence

    This function is pure: no I/O, no side effects.
    """
    if user_needs is None:
        user_needs = {}

    # Index evidence by fact_key for easy lookup
    by_key = {e.fact_key: e for e in evidence}

    present = [e for e in evidence if e.present]
    absent_keys = [e.fact_key for e in evidence if not e.present]

    # --- Rule 1: no data at all ---
    if not present:
        return ScoringOutput(
            verdict="unknown",
            confidence="none",
            decision_advice="do_not_rely",
            reasons=[],
            risks=["No accessibility data found for this location in OpenStreetMap."],
            unknowns=absent_keys,
            evidence_count=0,
        )

    wc = by_key.get("wheelchair_access")
    step_free = by_key.get("entrance_step_free")
    ramp = by_key.get("entrance_ramp")
    step_count_ev = by_key.get("step_count")

    # --- Rule 2: explicitly inaccessible ---
    if wc and wc.present and wc.interpreted_value == "no":
        return ScoringOutput(
            verdict="unlikely_accessible",
            confidence="high",
            decision_advice="choose_alternative",
            reasons=[Reason(
                text="OpenStreetMap records this place as not wheelchair accessible.",
                fact_key="wheelchair_access",
                source_type="osm",
            )],
            risks=[],
            unknowns=absent_keys,
            evidence_count=len(present),
        )

    # --- Rule 3: risky — contradictory signals ---
    # wheelchair=yes but steps are also recorded
    if wc and wc.present and wc.interpreted_value == "yes":
        has_step_contradiction = (
            (step_free and step_free.present and step_free.interpreted_value == "no")
            or (step_count_ev and step_count_ev.present and step_count_ev.interpreted_value == "no")
        )
        if has_step_contradiction:
            return ScoringOutput(
                verdict="risky",
                confidence="low",
                decision_advice="verify_first",
                reasons=[Reason(
                    text="Tagged as wheelchair accessible, but steps are also recorded at this location.",
                    fact_key="wheelchair_access",
                    source_type="osm",
                )],
                risks=[
                    "Accessibility tag and entrance data are contradictory. "
                    "The actual situation is unclear — verify before visiting.",
                ],
                unknowns=absent_keys,
                evidence_count=len(present),
            )

    # --- Rule 4: wheelchair=yes ---
    if wc and wc.present and wc.interpreted_value == "yes":
        reasons: List[Reason] = [Reason(
            text="Recorded as wheelchair accessible in OpenStreetMap.",
            fact_key="wheelchair_access",
            source_type="osm",
        )]
        risks: List[str] = []

        # Count supporting positive evidence
        supporting = [
            e for e in present
            if e.fact_key != "wheelchair_access" and e.interpreted_value in ("yes",)
        ]
        for s in supporting:
            reasons.append(Reason(
                text=f"Supported by: {s.fact_key.replace('_', ' ')} = {s.raw_value}.",
                fact_key=s.fact_key,
                source_type=s.source_type,
            ))

        # Freshness risk
        freshness_label = _classify_freshness(wc.freshness_days)
        if freshness_label in ("stale", "very_stale"):
            risks.append(
                f"Accessibility data is more than {wc.freshness_days // 365} year(s) old. "
                "Conditions may have changed."
            )

        # Confidence based on signal density + freshness
        if len(supporting) >= 2 and freshness_label in ("fresh", "recent", "aging"):
            confidence = "high"
            decision_advice = "good_option"
        elif len(supporting) >= 1 or freshness_label in ("fresh", "recent"):
            confidence = "medium"
            decision_advice = "verify_first"
        else:
            confidence = "low"
            risks.append(
                "Only the basic wheelchair tag is recorded. "
                "Entrance details are not confirmed."
            )
            decision_advice = "verify_first"

        # User needs override
        if user_needs.get("step_free_required") and not (step_free and step_free.present):
            if confidence == "high":
                confidence = "medium"
            risks.append("Step-free entrance is not confirmed. Verify before visiting.")

        return ScoringOutput(
            verdict="likely_accessible",
            confidence=confidence,
            decision_advice=decision_advice,
            reasons=reasons,
            risks=risks,
            unknowns=absent_keys,
            evidence_count=len(present),
        )

    # --- Rule 5: wheelchair=limited ---
    if wc and wc.present and wc.interpreted_value == "limited":
        reasons = [Reason(
            text="Recorded as having limited wheelchair accessibility — some barriers may be present.",
            fact_key="wheelchair_access",
            source_type="osm",
        )]
        if ramp and ramp.present and ramp.interpreted_value == "yes":
            reasons.append(Reason(
                text="A ramp is recorded at this location.",
                fact_key="entrance_ramp",
                source_type="osm",
            ))
        return ScoringOutput(
            verdict="partially_accessible",
            confidence="medium",
            decision_advice="verify_first",
            reasons=reasons,
            risks=["Limited accessibility may mean a small step, narrow passage, or manual door."],
            unknowns=absent_keys,
            evidence_count=len(present),
        )

    # --- Rule 6: other signals present but no definitive wheelchair tag ---
    return ScoringOutput(
        verdict="unknown",
        confidence="low",
        decision_advice="verify_first",
        reasons=[],
        risks=[
            f"Accessibility data exists ({len(present)} signal(s)) but is insufficient "
            "to determine overall wheelchair accessibility."
        ],
        unknowns=absent_keys,
        evidence_count=len(present),
    )