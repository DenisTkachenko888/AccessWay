from datetime import timezone

from app.models import (
    AddressSearchResponse,
    DataProvenance,
    Evidence,
    GeocodedLocation,
)


def test_model_default_timestamps_are_timezone_aware_utc():
    evidence = Evidence(
        fact_key="wheelchair_access",
        source_type="osm",
        source_detail="test",
        interpreted_value="unknown",
        present=False,
        confidence="none",
    )
    provenance = DataProvenance(
        source_type="osm",
        freshness_label="unknown",
        evidence_checked=1,
        evidence_present=0,
        evidence_absent=1,
    )

    assert evidence.collected_at.tzinfo is not None
    assert evidence.collected_at.utcoffset() == timezone.utc.utcoffset(evidence.collected_at)
    assert provenance.collected_at.tzinfo is not None
    assert provenance.collected_at.utcoffset() == timezone.utc.utcoffset(provenance.collected_at)


def test_address_search_response_has_stable_contract():
    response = AddressSearchResponse(
        query="Красная площадь, Москва",
        location=GeocodedLocation(
            lat=55.7535908,
            lon=37.6215013,
            display_name="Красная площадь, Москва",
            osm_type="relation",
            osm_id=1577673,
        ),
        results=[],
    )

    payload = response.model_dump()
    assert payload["query"] == "Красная площадь, Москва"
    assert payload["location"]["osm_type"] == "relation"
    assert payload["location"]["osm_id"] == 1577673
    assert payload["results"] == []
