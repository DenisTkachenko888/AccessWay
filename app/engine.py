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
    "entrance_step_free": ["step_count", "entrance"],
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

# Display-only age bands, not thresholds for confidence or verification.
_FRESHNESS_THRESHOLDS = [
    (180, "fresh"),
    (365, "recent"),
    (730, "aging"),
    (1095, "stale"),
]


def _as_utc(value: datetime) -> datetime:
    """Treat legacy naive timestamps as UTC, never as machine-local time."""
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _classify_freshness(days: Optional[int]) -> str:
    if days is None or days < 0:
        return "unknown"
    for threshold, label in _FRESHNESS_THRESHOLDS:
        if days <= threshold:
            return label
    return "very_stale"


def _interpret_osm_value(fact_key: str, raw: Optional[str], tag_key: Optional[str] = None) -> str:
    """Normalize a raw OSM tag value to yes/no/limited/unknown."""
    if raw is None:
        return "unknown"
    raw = raw.strip().lower()

    if fact_key in {"entrance_step_free", "step_count"}:
        if tag_key == "step_count":
            # Keep the count in raw_value; the existing binary contract describes
            # presence of steps, not accessibility. Reject non-integer counts.
            if not raw or any(char not in "0123456789" for char in raw):
                return "unknown"
            has_steps = any(char != "0" for char in raw)
            if fact_key == "step_count":
                return "yes" if has_steps else "no"
            return "no" if has_steps else "yes"
        if tag_key == "entrance":
            # Retain the existing explicit level interpretation. Other entrance
            # types (including yes/no) say nothing about the number of steps.
            return "yes" if raw == "level" else "unknown"
        return "unknown"

    if fact_key == "barrier_present":
        # Barrier presence is not a positive accessibility signal.
        return raw if raw in {"yes", "no"} else "unknown"

    if raw in _POSITIVE_VALUES:
        return "yes"
    if raw in _NEGATIVE_VALUES:
        return "no"
    if raw in _LIMITED_VALUES:
        return "limited"
    return "unknown"


def _signal_confidence(interpreted: str) -> str:
    """
    Determine confidence for a single evidence item.
    Rules:
    - If tag is absent: none
    - Uninterpreted values have no usable confidence.
    - An interpreted OSM claim is unverified: low regardless of source age.
    This describes support for the physical fact, not fidelity of tag copying.
    """
    if interpreted == "unknown":
        return "none"

    return "low"


def extract_evidence(
    tags: Dict[str, str],
    osm_last_edit: Optional[datetime] = None,
    source_id: Optional[str] = None,
    collected_at: Optional[datetime] = None,
) -> List[Evidence]:
    """
    Extract Evidence from raw OSM tags.

    Returns Evidence for every checked fact_key, including absent tags.
    Explicit entrance reports may add a second item for entrance_step_free;
    they must not be discarded in favour of an unscoped step count.
    Absent tags produce Evidence with present=False and confidence='none'.
    Absence is data, not nothing.

    Args:
        tags: Raw OSM tag dict from Overpass response
        osm_last_edit: Timestamp of the OSM element's last edit (from metadata)
        source_id: OSM element ID as string
        collected_at: When we fetched this data (defaults to now)
    """
    now = _as_utc(collected_at or datetime.now(timezone.utc))
    freshness_days: Optional[int] = None
    if osm_last_edit:
        osm_last_edit = _as_utc(osm_last_edit)
        delta = now - osm_last_edit
        # Future metadata is invalid/unknown, not evidence of freshness.
        freshness_days = delta.days if delta.total_seconds() >= 0 else None

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
        interpreted = _interpret_osm_value(fact_key, raw_value, matched_tag) if present else "unknown"
        confidence = _signal_confidence(interpreted) if present else "none"

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
        # Preserve an explicit entrance claim alongside the count. The count
        # may describe another entrance; priority must not erase this report.
        if (fact_key == "entrance_step_free" and matched_tag == "step_count"
                and tags.get("entrance", "").strip().lower() == "level"):
            evidence_list.append(evidence_list[-1].model_copy(update={
                "raw_value": tags["entrance"],
                "source_detail": f"OSM tag entrance={tags['entrance']}",
                "interpreted_value": "yes",
                "confidence": _signal_confidence("yes"),
            }))

    return evidence_list


def build_provenance(
    evidence: List[Evidence],
    osm_last_edit: Optional[datetime] = None,
) -> DataProvenance:
    """Build a DataProvenance record from an evidence list."""
    present_count = sum(1 for e in evidence if e.present)
    absent_count = sum(1 for e in evidence if not e.present)
    ages = [e.freshness_days for e in evidence]
    freshness_days = (
        max(ages) if ages and all(age is not None and age >= 0 for age in ages) else None
    )

    return DataProvenance(
        source_type="osm",
        collected_at=max((_as_utc(e.collected_at) for e in evidence),
                         default=datetime.now(timezone.utc)),
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

    Confidence is qualitative, not a probability or field verification.
    Low is the ceiling until a verification protocol is implemented. Neither
    Evidence.confidence, source_type, tag counts nor edit dates establish that
    protocol. Medium/high remain reserved, even for nominally official sources.
    Source age is reported separately and does not determine confidence.
    Conflicting interpreted values are retained, never resolved by input order
    or timestamps (an OSM edit is not a new observation of every property).
    Unknowns include any absent/uninterpreted input and unresolved conflicts.
    Evidence count counts distinct present source claims, ignoring collection
    time so fetching the same claim again does not inflate the count.

    This function is pure: no I/O, no side effects.
    """
    if user_needs is None:
        user_needs = {}

    grouped: Dict[str, List[Evidence]] = {}
    for item in evidence:
        grouped.setdefault(item.fact_key, []).append(item)
    values = {
        key: {e.interpreted_value for e in items
              if e.present and e.interpreted_value != "unknown"}
        for key, items in grouped.items()
    }
    conflicts = sorted(key for key, choices in values.items() if len(choices) > 1)
    # Coexisting reports may describe different entrances. This is uncertain
    # applicability, not proof of logical contradiction or no usable entrance.
    entrance_scope_uncertain = bool(
        values.get("entrance_step_free", set())
        & values.get("step_count", set()) & {"yes", "no"}
    )
    # Representatives only select agreed values, not winners between sources.
    by_key = {
        key: next(e for e in items if e.present and e.interpreted_value in values[key])
        for key, items in grouped.items() if len(values[key]) == 1
    }
    present = [e for e in evidence if e.present]
    known = [e for e in present if e.interpreted_value != "unknown"]
    unknown_keys = sorted(set(conflicts) | {
        e.fact_key for e in evidence if not e.present or e.interpreted_value == "unknown"
    } | ({"entrance_step_free"} if entrance_scope_uncertain else set()))
    if user_needs.get("step_free_required") and not values.get("entrance_step_free"):
        unknown_keys = sorted(set(unknown_keys) | {"entrance_step_free"})

    def finish(verdict: str, risks: List[str]) -> ScoringOutput:
        """Apply one confidence/advice policy to every verdict branch."""
        risks = list(risks)
        requires_steps = user_needs.get("step_free_required", False)
        confidence = "none" if not known else "low"
        step_values = values.get("entrance_step_free", set())
        if requires_steps and (step_values != {"yes"} or entrance_scope_uncertain):
            risks.append("Step-free entrance is not confirmed. Verify before visiting.")
        if ("yes" in values.get("step_count", set())
                or "no" in step_values or "yes" in values.get("barrier_present", set())):
            risks.append("Sources report steps or a barrier; suitability must be checked.")
        if any(e.freshness_days is None or e.freshness_days < 0 for e in known):
            risks.append("Some source ages are unknown; accessibility has not been verified.")
        if any(_classify_freshness(e.freshness_days) in {"stale", "very_stale"}
               for e in known):
            risks.append("Some source records are over two years old; this describes "
                         "source age, not the date of an accessibility inspection.")
        if any(e.source_type == "osm" for e in known):
            risks.append("An OSM edit date is not confirmation of an accessibility inspection.")
        advice = "verify_first"
        if confidence == "none":
            advice = "do_not_rely"
        # Statements are explicitly attributed to the source, including both
        # sides of a conflict. No source is silently labelled as verified.
        claims = sorted({(e.fact_key, e.source_type, e.source_id or "",
                          e.source_detail, e.raw_value or "", e.interpreted_value)
                         for e in known})
        reasons = [Reason(
            text=f"Source {source} ({source_id or detail}) reports {key}={value}"
                 f" (raw: {raw}); this is not independent verification.",
            fact_key=key, source_type=source,
        ) for key, source, source_id, detail, raw, value in claims]
        return ScoringOutput(verdict=verdict, confidence=confidence, decision_advice=advice,
                      reasons=reasons, risks=sorted(set(risks)),
                      unknowns=unknown_keys,
                      evidence_count=len({(e.fact_key, e.source_type, e.source_id,
                                           e.source_detail, e.raw_value, e.interpreted_value)
                                          for e in present}))

    if conflicts or entrance_scope_uncertain:
        conflict_risks = [
            f"Differing source reports for {key}; their applicability is unresolved. Verify before visiting."
            for key in conflicts if key not in {"entrance_step_free", "step_count"}
        ]
        if entrance_scope_uncertain or set(conflicts) & {"entrance_step_free", "step_count"}:
            conflict_risks.append(
                "Entrance and step reports may concern different entrances. "
                "Their relationship is unknown; verify which entrance is usable. "
                "This is a potential risk, not a confirmed contradiction."
            )
        return finish(verdict="risky", risks=conflict_risks)

    # Preserve the existing verdict hierarchy; finish applies policy uniformly.
    if not known:
        return finish(verdict="unknown", risks=[
            "No interpretable accessibility data is available."
        ])

    wc = by_key.get("wheelchair_access")
    step_free = by_key.get("entrance_step_free")
    step_count_ev = by_key.get("step_count")
    has_barrier = (
        (step_free is not None and step_free.interpreted_value == "no")
        or (step_count_ev is not None and step_count_ev.interpreted_value == "yes")
        or values.get("barrier_present") == {"yes"}
    )

    if wc and wc.interpreted_value == "no":
        return finish(verdict="unlikely_accessible", risks=[])
    if wc and wc.interpreted_value == "yes":
        if has_barrier:
            return finish(verdict="risky", risks=[
                "Accessibility and barrier reports do not identify a usable entrance. "
                "Steps may concern a different entrance; verify access before visiting."
            ])
        risks = []
        if not (step_free and step_free.interpreted_value == "yes"):
            risks.append("Entrance details are not confirmed.")
        return finish(verdict="likely_accessible", risks=risks)
    if wc and wc.interpreted_value == "limited":
        return finish(verdict="partially_accessible", risks=[
            "The source reports limited accessibility; the exact limitations need verification."
        ])
    return finish(verdict="unknown", risks=[
        "Source reports are insufficient to determine overall wheelchair accessibility."
    ])
