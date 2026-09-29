from datetime import datetime, timezone

from app.main import _distance_m, _rank_reports
from app.models import DataProvenance, PlaceReport, ScoringOutput


def make_report(
    name: str,
    verdict: str,
    confidence: str,
    lat: float,
    lon: float,
) -> PlaceReport:
    return PlaceReport(
        name=name,
        category="cafe",
        lat=lat,
        lon=lon,
        osm_id=1,
        osm_type="node",
        evidence=[],
        scoring=ScoringOutput(
            verdict=verdict,
            confidence=confidence,
            decision_advice=(
                "good_option"
                if verdict == "likely_accessible" and confidence == "high"
                else "verify_first"
            ),
            reasons=[],
            risks=[],
            unknowns=[],
            evidence_count=0,
        ),
        provenance=DataProvenance(
            source_type="osm",
            collected_at=datetime.now(timezone.utc),
            freshness_label="unknown",
            evidence_checked=0,
            evidence_present=0,
            evidence_absent=0,
        ),
    )


def test_distance_is_zero_for_same_coordinate():
    assert _distance_m(55.75, 37.61, 55.75, 37.61) == 0


def test_distance_is_symmetric():
    a_to_b = _distance_m(55.75, 37.61, 55.76, 37.62)
    b_to_a = _distance_m(55.76, 37.62, 55.75, 37.61)
    assert abs(a_to_b - b_to_a) < 1e-9


def test_accessibility_beats_distance():
    origin_lat = 55.75
    origin_lon = 37.61

    closer_unknown = make_report(
        "Closer but unknown",
        "unknown",
        "low",
        55.7501,
        37.6101,
    )
    farther_accessible = make_report(
        "Farther and accessible",
        "likely_accessible",
        "medium",
        55.755,
        37.615,
    )

    ranked = _rank_reports(
        [closer_unknown, farther_accessible],
        origin_lat,
        origin_lon,
    )

    assert ranked[0].name == "Farther and accessible"


def test_confidence_breaks_tie_before_distance():
    origin_lat = 55.75
    origin_lon = 37.61

    closer_low = make_report(
        "Closer low confidence",
        "likely_accessible",
        "low",
        55.7501,
        37.6101,
    )
    farther_high = make_report(
        "Farther high confidence",
        "likely_accessible",
        "high",
        55.752,
        37.612,
    )

    ranked = _rank_reports(
        [closer_low, farther_high],
        origin_lat,
        origin_lon,
    )

    assert ranked[0].name == "Farther high confidence"


def test_distance_breaks_equal_accessibility_tie():
    origin_lat = 55.75
    origin_lon = 37.61

    farther = make_report(
        "Farther",
        "likely_accessible",
        "medium",
        55.76,
        37.62,
    )
    closer = make_report(
        "Closer",
        "likely_accessible",
        "medium",
        55.7502,
        37.6102,
    )

    ranked = _rank_reports([farther, closer], origin_lat, origin_lon)

    assert ranked[0].name == "Closer"
