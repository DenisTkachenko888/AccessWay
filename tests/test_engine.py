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
from itertools import permutations, product

from app.engine import build_provenance, extract_evidence, score, _signal_confidence
from app.models import Evidence


@pytest.mark.parametrize("entrance", ["yes", "no", "main", "secondary", "0", "1"])
def test_entrance_type_does_not_confirm_step_free_access(entrance):
    evidence = extract_evidence({"wheelchair": "yes", "entrance": entrance})
    item = next(e for e in evidence if e.fact_key == "entrance_step_free")
    assert item.present
    assert item.interpreted_value == "unknown"
    result = score(evidence, user_needs={"step_free_required": True})
    assert "entrance_step_free" in result.unknowns
    assert any("Step-free entrance is not confirmed" in risk for risk in result.risks)
    assert all(r.fact_key != "entrance_step_free" for r in result.reasons)


@pytest.mark.parametrize("entrance", [None, "yes", "main", "level"])
@pytest.mark.parametrize("count", ["0", "1", "3", " 12 "])
def test_step_count_is_preserved_regardless_of_entrance(entrance, count):
    tags = {"wheelchair": "yes", "step_count": count}
    if entrance is not None:
        tags["entrance"] = entrance
    evidence = extract_evidence(tags)
    facts = {e.fact_key: e for e in reversed(evidence)}
    has_steps = int(count) > 0
    assert facts["step_count"].interpreted_value == ("yes" if has_steps else "no")
    assert facts["entrance_step_free"].interpreted_value == ("no" if has_steps else "yes")
    assert facts["step_count"].raw_value == count
    assert facts["entrance_step_free"].source_detail == f"OSM tag step_count={count}"
    result = score(evidence)
    assert result.verdict == ("risky" if has_steps else "likely_accessible")
    assert any(r.fact_key == "step_count" for r in result.reasons)


@pytest.mark.parametrize("count", ["", "yes", "no", "-1", "1.5", "unknown", "1;2"])
def test_invalid_step_counts_remain_unknown(count):
    evidence = extract_evidence({"step_count": count, "entrance": "yes"})
    result = score(evidence)
    for key in ("step_count", "entrance_step_free"):
        item = next(e for e in evidence if e.fact_key == key)
        assert item.interpreted_value == "unknown"
        assert key in result.unknowns
    assert result.verdict == "unknown"


@pytest.mark.parametrize("support", [{}, {"entrance": "level"}])
@pytest.mark.parametrize("freshness", [None, datetime(2026, 1, 1, tzinfo=timezone.utc)])
def test_barrier_does_not_increase_confidence(support, freshness):
    tags = {"wheelchair": "yes", **support}
    collected = datetime(2026, 2, 1, tzinfo=timezone.utc)
    baseline = score(extract_evidence(tags, freshness, collected_at=collected))
    evidence = extract_evidence({**tags, "barrier": "yes"}, freshness, collected_at=collected)
    barrier = next(e for e in evidence if e.fact_key == "barrier_present")
    assert barrier.interpreted_value == "yes"
    result = score(evidence)
    levels = {"none": 0, "low": 1, "medium": 2, "high": 3}
    assert levels[result.confidence] <= levels[baseline.confidence]
    assert result.verdict == "risky"
    assert result.decision_advice == baseline.decision_advice
    assert any(r.fact_key == "barrier_present" for r in result.reasons)


@pytest.mark.parametrize("value,verdict", [("yes", "risky"), ("no", "likely_accessible")])
def test_scoring_step_presence_without_derived_step_free(value, verdict):
    result = score([
        make_evidence("wheelchair_access", "yes"),
        make_evidence("step_count", value),
    ])
    assert result.verdict == verdict


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


@pytest.mark.parametrize("key", ["wheelchair_access", "surface_type", "kerb_type",
                                     "accessible_toilet", "custom_fact"])
@pytest.mark.parametrize("present", [True, False])
def test_all_uninterpreted_facts_are_unknown(key, present):
    result = score([make_evidence(key, "unknown", present=present)])
    assert result.unknowns == [key]
    assert result.verdict == "unknown"
    assert result.confidence == "none"
    assert result.decision_advice == "do_not_rely"
    assert result.reasons == []


@pytest.mark.parametrize("value,verdict", [
    ("yes", "likely_accessible"), ("no", "unlikely_accessible"),
    ("limited", "partially_accessible"),
])
@pytest.mark.parametrize("age,confidence", [(0, "low"), (730, "low"),
                                           (731, "low"), (2000, "low"), (None, "low")])
def test_confidence_is_independent_of_age_for_all_wheelchair_values(value, verdict, age, confidence):
    result = score([make_evidence("wheelchair_access", value, freshness_days=age)])
    assert result.verdict == verdict
    assert result.confidence == confidence
    assert result.decision_advice == "verify_first"


@pytest.mark.parametrize("quality", ["low", "none"])
def test_weak_evidence_cannot_be_promoted_by_freshness(quality):
    result = score([make_evidence("wheelchair_access", "yes", confidence=quality)])
    assert result.confidence == "low"
    assert result.decision_advice == "verify_first"


def test_repeated_collection_and_supporters_do_not_inflate_confidence():
    wc = make_evidence("wheelchair_access", "yes", confidence="high")
    baseline = score([wc])
    repeat = wc.model_copy(update={"collected_at": wc.collected_at + timedelta(days=1)})
    assert score([wc, repeat]) == baseline
    enriched = score([wc, make_evidence("entrance_ramp", "yes"),
                      make_evidence("accessible_toilet", "yes")])
    assert enriched.confidence == baseline.confidence == "low"
    assert enriched.decision_advice == "verify_first"


@pytest.mark.parametrize("key", ["wheelchair_access", "entrance_step_free", "accessible_toilet"])
def test_conflicts_are_explicit_and_permutation_invariant(key):
    items = [make_evidence(key, "yes"), make_evidence(key, "no"),
             make_evidence("wheelchair_access", "limited")]
    expected = score(items)
    assert expected.verdict == "risky"
    assert expected.confidence == "low"
    assert expected.decision_advice == "verify_first"
    assert key in expected.unknowns
    assert any("reports" in risk for risk in expected.risks)
    assert {"yes", "no"} <= {
        value for value in ("yes", "no")
        if any(f"{key}={value}" in reason.text for reason in expected.reasons)
    }
    for order in permutations(items):
        assert score(list(order)) == expected


def test_agreeing_evidence_uses_weakest_age_independent_of_order():
    items = [make_evidence("wheelchair_access", "yes", freshness_days=10),
             make_evidence("wheelchair_access", "yes", freshness_days=900),
             make_evidence("wheelchair_access", "unknown", present=False)]
    expected = score(items)
    assert expected.confidence == "low"
    assert "wheelchair_access" in expected.unknowns
    for order in permutations(items):
        assert score(list(order)) == expected


@pytest.mark.parametrize("step,age,expected", [("yes", 0, "low"),
    ("yes", 900, "low"), ("no", 0, "low"), ("unknown", 0, "low")])
def test_required_step_free_uses_value_and_strength(step, age, expected):
    result = score([make_evidence("wheelchair_access", "yes"),
                    make_evidence("entrance_step_free", step, freshness_days=age)],
                   user_needs={"step_free_required": True})
    assert result.confidence == expected
    assert result.decision_advice == "verify_first"
    if step != "yes":
        assert any("Step-free entrance is not confirmed" in risk for risk in result.risks)


def test_reasons_attribute_non_osm_source_without_claiming_verification():
    item = make_evidence("wheelchair_access", "no").model_copy(update={
        "source_type": "official", "source_id": "museum-123"
    })
    reason = score([item]).reasons[0]
    assert reason.source_type == "official"
    assert "museum-123" in reason.text
    assert "not independent verification" in reason.text
    assert "OpenStreetMap" not in reason.text


def test_fresh_osm_edit_does_not_create_high_confidence():
    evidence = extract_evidence({"wheelchair": "yes"}, osm_last_edit=fresh_meta())
    wc = next(e for e in evidence if e.fact_key == "wheelchair_access")
    assert wc.confidence == "low"
    result = score(evidence)
    assert result.confidence == "low"
    assert any("not confirmation" in risk for risk in result.risks)


@pytest.mark.parametrize("age", [-1, None, 0, 730, 731])
def test_extraction_and_score_do_not_promote_unverified_claims(age):
    confidence = _signal_confidence("yes")
    item = make_evidence("wheelchair_access", "yes", confidence=confidence,
                         freshness_days=age)
    assert score([item]).confidence == confidence


@pytest.mark.parametrize("future_offset", [timedelta(seconds=1), timedelta(days=10)])
def test_future_source_date_is_unknown_not_fresh(future_offset):
    collected = datetime(2026, 11, 12, tzinfo=timezone.utc)
    items = extract_evidence({"wheelchair": "yes"},
                             collected + future_offset, collected_at=collected)
    assert all(e.freshness_days is None for e in items)
    assert score(items).confidence == "low"
    assert build_provenance(items).freshness_label == "unknown"


@pytest.mark.parametrize("naive_source,naive_collection", [(True, False), (False, True), (True, True)])
def test_legacy_naive_dates_are_consistently_utc(naive_source, naive_collection):
    collected = datetime(2026, 11, 12, tzinfo=timezone.utc)
    source = collected - timedelta(days=5)
    items = extract_evidence(
        {"wheelchair": "yes"},
        source.replace(tzinfo=None) if naive_source else source,
        collected_at=collected.replace(tzinfo=None) if naive_collection else collected,
    )
    assert items[0].freshness_days == 5
    assert items[0].collected_at == collected
    assert items[0].observed_at == source


@pytest.mark.parametrize("value", ["yes", "no"])
@pytest.mark.parametrize("wheelchair", [None, "yes", "no", "limited"])
def test_cross_fact_step_disagreement_is_not_silently_accepted(value, wheelchair):
    items = [make_evidence("entrance_step_free", value),
             make_evidence("step_count", value)]
    if wheelchair:
        items.append(make_evidence("wheelchair_access", wheelchair))
    expected = score(items, user_needs={"step_free_required": True})
    assert expected.verdict == "risky"
    assert expected.confidence == "low"
    assert expected.decision_advice == "verify_first"
    assert "entrance_step_free" in expected.unknowns
    assert "step_count" not in expected.unknowns
    assert any("different entrances" in risk for risk in expected.risks)
    for order in permutations(items):
        assert score(list(order), user_needs={"step_free_required": True}) == expected


@pytest.mark.parametrize("ages,expected", [([1, 900], "stale"), ([1, None], "unknown"),
                                          ([1, -1], "unknown")])
def test_provenance_age_is_order_independent_and_does_not_refresh_collection(ages, expected):
    collected = datetime(2020, 1, 1, tzinfo=timezone.utc)
    items = [make_evidence("wheelchair_access", "yes", freshness_days=age)
             .model_copy(update={"collected_at": collected}) for age in ages]
    for order in permutations(items):
        provenance = build_provenance(list(order))
        assert provenance.collected_at == collected
        assert provenance.freshness_label == expected


def test_advice_invariants_across_wheelchair_steps_age_and_user_need():
    # Check policy properties across 384 combinations rather than duplicating
    # branch implementation in the expected result.
    for wheelchair, step, age, required, quality in product(
        ("yes", "no", "limited", "unknown"), ("yes", "no", "unknown"),
        (0, 730, 731, None), (False, True), ("medium", "low", "none", "high"),
    ):
        items = [make_evidence("wheelchair_access", wheelchair, confidence=quality,
                               freshness_days=age),
                 make_evidence("entrance_step_free", step, confidence=quality,
                               freshness_days=age)]
        result = score(items, user_needs={"step_free_required": required})
        assert result.confidence != "high"
        assert result.decision_advice != "good_option"
        assert result.decision_advice != "choose_alternative"
        if result.confidence == "none":
            assert result.decision_advice == "do_not_rely"
        if required and step != "yes":
            assert result.confidence in {"low", "none"}
            assert any("Step-free entrance is not confirmed" in risk for risk in result.risks)
        assert score(list(reversed(items)),
                     user_needs={"step_free_required": required}) == result


@pytest.mark.parametrize("source", ["osm", "official", "user_confirmation", "visual_analysis", "derived"])
@pytest.mark.parametrize("claimed_confidence", ["none", "low", "medium", "high"])
def test_source_label_and_claimed_confidence_are_not_verification(source, claimed_confidence):
    item = make_evidence("wheelchair_access", "yes", confidence=claimed_confidence)
    item = item.model_copy(update={"source_type": source})
    result = score([item])
    assert result.confidence == "low"
    assert result.decision_advice == "verify_first"
    assert result.reasons[0].source_type == source


def test_unscoped_entrances_preserve_claims_without_asserting_contradiction():
    items = extract_evidence({"wheelchair": "yes", "entrance": "level", "step_count": "3"})
    entrance_items = [e for e in items if e.fact_key == "entrance_step_free"]
    assert {(e.source_detail, e.interpreted_value) for e in entrance_items} == {
        ("OSM tag entrance=level", "yes"), ("OSM tag step_count=3", "no")
    }
    result = score(items, user_needs={"step_free_required": True})
    assert result.verdict == "risky"
    assert result.confidence == "low"
    assert result.decision_advice == "verify_first"
    assert "entrance_step_free" in result.unknowns
    assert any("different entrances" in risk for risk in result.risks)
    assert any("not a confirmed contradiction" in risk for risk in result.risks)
    assert any("entrance_step_free=yes" in r.text for r in result.reasons)
    assert any("step_count=yes" in r.text for r in result.reasons)
    assert score(list(reversed(items)), user_needs={"step_free_required": True}) == result


def test_missing_required_fact_is_listed_even_with_sparse_evidence():
    result = score([make_evidence("wheelchair_access", "yes")],
                   user_needs={"step_free_required": True})
    assert "entrance_step_free" in result.unknowns
    assert result.decision_advice == "verify_first"


def test_source_fidelity_is_preserved_without_promoting_accessibility_confidence():
    collected = datetime(2026, 11, 12, tzinfo=timezone.utc)
    items = extract_evidence({"wheelchair": "yes"}, collected,
                             source_id="node/123", collected_at=collected)
    wc = next(e for e in items if e.fact_key == "wheelchair_access")
    assert wc.raw_value == "yes"
    assert wc.source_detail == "OSM tag wheelchair=yes"
    assert wc.source_id == "node/123"
    assert wc.freshness_days == 0
    assert wc.confidence == score(items).confidence == "low"


def test_source_age_warning_is_separate_from_confidence():
    recent = make_evidence("wheelchair_access", "yes", freshness_days=730)
    old = recent.model_copy(update={"freshness_days": 731})
    recent_result, old_result = score([recent]), score([old])
    assert recent_result.confidence == old_result.confidence == "low"
    assert recent_result.verdict == old_result.verdict
    assert recent_result.decision_advice == old_result.decision_advice
    assert not any("two years" in risk for risk in recent_result.risks)
    assert any("two years" in risk for risk in old_result.risks)


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

    def test_stale_osm_data_remains_low_confidence(self):
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

    def test_wheelchair_yes_alone_low_confidence(self):
        evidence = [make_evidence("wheelchair_access", "yes")]
        result = score(evidence)
        assert result.verdict == "likely_accessible"
        assert result.confidence == "low"

    def test_wheelchair_yes_with_two_supporters_stays_low(self):
        evidence = [
            make_evidence("wheelchair_access", "yes"),
            make_evidence("entrance_step_free", "yes"),
            make_evidence("entrance_ramp", "yes"),
        ]
        result = score(evidence)
        assert result.verdict == "likely_accessible"
        assert result.confidence == "low"
        assert result.decision_advice == "verify_first"

    def test_wheelchair_no_returns_unlikely_low(self):
        evidence = [make_evidence("wheelchair_access", "no")]
        result = score(evidence)
        assert result.verdict == "unlikely_accessible"
        assert result.confidence == "low"
        assert result.decision_advice == "verify_first"

    def test_wheelchair_limited_returns_partially_accessible(self):
        evidence = [make_evidence("wheelchair_access", "limited")]
        result = score(evidence)
        assert result.verdict == "partially_accessible"
        assert result.confidence == "low"

    def test_risky_verdict_on_unscoped_barrier_report(self):
        """wheelchair=yes + step recorded = risky, not likely_accessible."""
        evidence = [
            make_evidence("wheelchair_access", "yes"),
            make_evidence("entrance_step_free", "no"),   # source reports a barrier
        ]
        result = score(evidence)
        assert result.verdict == "risky"
        assert result.confidence == "low"
        assert any("verify" in risk.lower() for risk in result.risks)

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

    def test_full_pipeline_age_changes_freshness_not_confidence(self):
        """Age is source metadata, not a basis for physical verification."""
        fresh_ev = extract_evidence({"wheelchair": "yes"}, osm_last_edit=fresh_meta())
        stale_ev = extract_evidence({"wheelchair": "yes"}, osm_last_edit=stale_meta())
        fresh_result = score(fresh_ev)
        stale_result = score(stale_ev)
        assert fresh_result.confidence == stale_result.confidence == "low"
        assert build_provenance(fresh_ev).freshness_label == "fresh"
        assert build_provenance(stale_ev).freshness_label == "stale"

    def test_full_pipeline_unscoped_steps_risk(self):
        """Reported steps require checking which entrance is usable."""
        evidence = extract_evidence({
            "wheelchair": "yes",
            "step_count": "2",   # may refer to another entrance
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
