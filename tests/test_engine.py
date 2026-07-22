"""
Unit tests for the AccessWay scoring engine.

Tests are organized into three groups:
    1. extract_evidence — signal extraction from raw OSM tags
    2. score            — verdict and confidence calculation
    3. adversarial      — cases that should catch subtle engine mistakes

All tests are pure: no I/O, no network, no database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytest

from app.engine import build_provenance, extract_evidence, score
from app.models import Evidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_evidence(
    fact_key: str,
    interpreted_value: str,
    present: bool = True,
    confidence: str = "medium",
    raw_value: Optional[str] = None,
    freshness_days: Optional[int] = 0,
) -> Evidence:
    """Build a minimal Evidence object for testing score() in isolation."""
    return Evidence(
        fact_key=fact_key,
        source_type="osm",
        source_detail="test fixture",
        raw_value=raw_value,
        interpreted_value=interpreted_value,
        present=present,
        confidence=confidence,
        freshness_days=freshness_days,
    )


def fresh_meta() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=30)


def stale_meta() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=900)


# ---------------------------------------------------------------------------
# Group 1: extract_evidence
# ---------------------------------------------------------------------------

class TestExtractEvidence:

    def test_empty_tags_all_absent(self):
        evidence = extract_evidence({})
        assert all(not e.present for e in evidence)
        assert all(e.confidence == "none" for e in evidence)
        assert all(e.interpreted_value == "unknown" for e in evidence)

    def test_wheelchair_yes_extracted(self):
        evidence = extract_evidence({"wheelchair": "yes"})
        wc = next(e for e in evidence if e.fact_key == "wheelchair_access")
        assert wc.present is True
        assert wc.interpreted_value == "yes"
        assert wc.raw_value == "yes"

    def test_wheelchair_no_extracted(self):
        evidence = extract_evidence({"wheelchair": "no"})
        wc = next(e for e in evidence if e.fact_key == "wheelchair_access")
        assert wc.interpreted_value == "no"

    def test_wheelchair_limited_extracted(self):
        evidence = extract_evidence({"wheelchair": "limited"})
        wc = next(e for e in evidence if e.fact_key == "wheelchair_access")
        assert wc.interpreted_value == "limited"

    def test_entrance_level_maps_to_step_free_yes(self):
        """OSM entrance=level means step-free — must not be misinterpreted."""
        evidence = extract_evidence({"entrance": "level"})
        sf = next(e for e in evidence if e.fact_key == "entrance_step_free")
        assert sf.present is True
        assert sf.interpreted_value == "yes"

    def test_step_count_zero_maps_to_step_free(self):
        evidence = extract_evidence({"step_count": "0"})
        sf = next(e for e in evidence if e.fact_key == "entrance_step_free")
        assert sf.interpreted_value == "yes"

    def test_step_count_nonzero_maps_to_step_not_free(self):
        evidence = extract_evidence({"step_count": "3"})
        sf = next(e for e in evidence if e.fact_key == "entrance_step_free")
        assert sf.interpreted_value == "no"

    def test_ramp_wheelchair_yes_preferred_over_ramp(self):
        """ramp:wheelchair=yes should win over ramp=yes."""
        evidence = extract_evidence({"ramp:wheelchair": "yes", "ramp": "yes"})
        ramp = next(e for e in evidence if e.fact_key == "entrance_ramp")
        assert ramp.present is True
        assert ramp.interpreted_value == "yes"

    def test_toilets_wheelchair_extracted(self):
        evidence = extract_evidence({"toilets:wheelchair": "yes"})
        toilet = next(e for e in evidence if e.fact_key == "accessible_toilet")
        assert toilet.present is True
        assert toilet.interpreted_value == "yes"

    def test_wrong_tag_entrance_step_free_not_in_osm(self):
        """entrance:step_free is NOT a standard OSM tag and must NOT match."""
        evidence = extract_evidence({"entrance:step_free": "yes"})
        sf = next(e for e in evidence if e.fact_key == "entrance_step_free")
        # Should be absent because we don't check for "entrance:step_free"
        assert sf.present is False

    def test_freshness_calculation(self):
        """Evidence should carry correct freshness_days."""
        osm_date = datetime.now(timezone.utc) - timedelta(days=45)
        evidence = extract_evidence({"wheelchair": "yes"}, osm_last_edit=osm_date)
        wc = next(e for e in evidence if e.fact_key == "wheelchair_access")
        assert wc.freshness_days is not None
        assert 44 <= wc.freshness_days <= 46

    def test_stale_data_degrades_to_low_confidence(self):
        osm_date = datetime.now(timezone.utc) - timedelta(days=800)
        evidence = extract_evidence({"wheelchair": "yes"}, osm_last_edit=osm_date)
        wc = next(e for e in evidence if e.fact_key == "wheelchair_access")
        assert wc.confidence == "low"

    def test_absent_tag_recorded_explicitly(self):
        """Absent tags must be in the evidence list with present=False."""
        evidence = extract_evidence({})
        fact_keys = {e.fact_key for e in evidence}
        assert "wheelchair_access" in fact_keys
        assert "entrance_step_free" in fact_keys
        assert "accessible_toilet" in fact_keys


# ---------------------------------------------------------------------------
# Group 2: score
# ---------------------------------------------------------------------------

class TestScore:

    def test_no_evidence_returns_unknown_none(self):
        evidence = [make_evidence("wheelchair_access", "unknown", present=False, confidence="none")]
        result = score(evidence)
        assert result.verdict == "unknown"
        assert result.confidence == "none"
        assert result.decision_advice == "do_not_rely"

    def test_wheelchair_yes_alone_medium_confidence(self):
        evidence = [make_evidence("wheelchair_access", "yes")]
        result = score(evidence)
        assert result.verdict == "likely_accessible"
        assert result.confidence in ("medium", "low")  # no supporting signals

    def test_wheelchair_yes_with_two_supporters_high_confidence(self):
        evidence = [
            make_evidence("wheelchair_access", "yes"),
            make_evidence("entrance_step_free", "yes"),
            make_evidence("entrance_ramp", "yes"),
        ]
        result = score(evidence)
        assert result.verdict == "likely_accessible"
        assert result.confidence == "high"
        assert result.decision_advice == "good_option"

    def test_wheelchair_no_returns_unlikely_high(self):
        evidence = [make_evidence("wheelchair_access", "no")]
        result = score(evidence)
        assert result.verdict == "unlikely_accessible"
        assert result.confidence == "high"
        assert result.decision_advice == "choose_alternative"

    def test_wheelchair_limited_returns_partially_accessible(self):
        evidence = [make_evidence("wheelchair_access", "limited")]
        result = score(evidence)
        assert result.verdict == "partially_accessible"
        assert result.confidence == "medium"

    def test_risky_verdict_on_contradiction(self):
        """wheelchair=yes + step recorded = risky, not likely_accessible."""
        evidence = [
            make_evidence("wheelchair_access", "yes"),
            make_evidence("entrance_step_free", "no"),   # steps confirmed
        ]
        result = score(evidence)
        assert result.verdict == "risky"
        assert result.confidence == "low"
        assert "contradictory" in result.risks[0].lower()

    def test_unknowns_list_populated(self):
        """Absent fact_keys must appear in the unknowns list."""
        evidence = [
            make_evidence("wheelchair_access", "yes"),
            make_evidence("accessible_toilet", "unknown", present=False, confidence="none"),
            make_evidence("entrance_step_free", "unknown", present=False, confidence="none"),
        ]
        result = score(evidence)
        assert "accessible_toilet" in result.unknowns
        assert "entrance_step_free" in result.unknowns

    def test_reasons_traceable_to_fact_key(self):
        """Every reason must reference a fact_key that exists in the evidence."""
        evidence = [
            make_evidence("wheelchair_access", "yes"),
            make_evidence("entrance_ramp", "yes"),
        ]
        result = score(evidence)
        evidence_keys = {e.fact_key for e in evidence}
        for reason in result.reasons:
            assert reason.fact_key in evidence_keys

    def test_user_needs_step_free_required_adds_risk(self):
        """If user requires step-free and it's not confirmed, risk should appear."""
        evidence = [make_evidence("wheelchair_access", "yes")]
        result = score(evidence, user_needs={"step_free_required": True})
        assert any("step-free" in r.lower() for r in result.risks)

    def test_other_signals_only_returns_unknown_low(self):
        """Ramp present but no wheelchair tag → unknown with low confidence."""
        evidence = [
            make_evidence("wheelchair_access", "unknown", present=False, confidence="none"),
            make_evidence("entrance_ramp", "yes"),
        ]
        result = score(evidence)
        assert result.verdict == "unknown"
        assert result.confidence == "low"


# ---------------------------------------------------------------------------
# Group 3: adversarial / integration
# ---------------------------------------------------------------------------

class TestAdversarial:

    def test_full_pipeline_no_tags(self):
        """A place with zero tags must return unknown with no hallucinated reasons."""
        evidence = extract_evidence({})
        result = score(evidence)
        assert result.verdict == "unknown"
        assert result.confidence == "none"
        assert result.reasons == []

    def test_full_pipeline_wheelchair_yes_fresh(self):
        """Fresh wheelchair=yes should produce likely_accessible."""
        evidence = extract_evidence({"wheelchair": "yes"}, osm_last_edit=fresh_meta())
        result = score(evidence)
        assert result.verdict == "likely_accessible"

    def test_full_pipeline_wheelchair_yes_stale_degrades(self):
        """Stale wheelchair=yes should have lower confidence than fresh."""
        fresh_ev = extract_evidence({"wheelchair": "yes"}, osm_last_edit=fresh_meta())
        stale_ev = extract_evidence({"wheelchair": "yes"}, osm_last_edit=stale_meta())
        fresh_result = score(fresh_ev)
        stale_result = score(stale_ev)
        confidence_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        assert confidence_order[stale_result.confidence] >= confidence_order[fresh_result.confidence]

    def test_full_pipeline_contradiction(self):
        """OSM marked accessible but steps confirmed — should surface as risky."""
        evidence = extract_evidence({
            "wheelchair": "yes",
            "step_count": "2",   # contradicts wheelchair=yes
        })
        result = score(evidence)
        assert result.verdict == "risky"

    def test_entrance_level_does_not_cause_false_contradiction(self):
        """entrance=level + wheelchair=yes should NOT trigger risky."""
        evidence = extract_evidence({
            "wheelchair": "yes",
            "entrance": "level",  # step-free, consistent with wheelchair=yes
        })
        result = score(evidence)
        assert result.verdict == "likely_accessible"
        assert result.verdict != "risky"

    def test_wrong_osm_tag_does_not_produce_false_signal(self):
        """entrance:step_free (non-standard tag) must not inflate confidence."""
        evidence_with_wrong_tag = extract_evidence({"entrance:step_free": "yes"})
        evidence_empty = extract_evidence({})
        sf_wrong = next(e for e in evidence_with_wrong_tag if e.fact_key == "entrance_step_free")
        sf_empty = next(e for e in evidence_empty if e.fact_key == "entrance_step_free")
        # Both should be absent — wrong tag should not match
        assert sf_wrong.present == sf_empty.present

    def test_provenance_reflects_absent_count(self):
        """Provenance should correctly count absent vs present signals."""
        evidence = extract_evidence({"wheelchair": "yes"})
        provenance = build_provenance(evidence)
        assert provenance.evidence_present >= 1
        assert provenance.evidence_absent >= 1
        assert provenance.evidence_checked == len(evidence)

    def test_score_never_hallucinates_claim(self):
        """No reason should appear when there is no supporting evidence."""
        evidence = extract_evidence({})
        result = score(evidence)
        # verdict unknown + no evidence = reasons must be empty
        assert result.reasons == []

    def test_risky_verdict_in_unknown_unknowns_list(self):
        """risky verdict should still report what is unknown."""
        evidence = [
            make_evidence("wheelchair_access", "yes"),
            make_evidence("entrance_step_free", "no"),
            make_evidence("accessible_toilet", "unknown", present=False, confidence="none"),
        ]
        result = score(evidence)
        assert result.verdict == "risky"
        assert "accessible_toilet" in result.unknowns