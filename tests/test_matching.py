from pathlib import Path

import pytest

from app.matching import (
    Inlet,
    MANUAL_SIGMA_M,
    distance_m,
    load_inlets,
    location_sigma,
    match,
    search_radius,
)

# Elizabeth St / Collins St intersection
ORIGIN = (-37.8136, 144.9631)
# Roughly 1 m of latitude in degrees
DEG_PER_M = 1 / 111_195


def inlet_north_of_origin(asset_number: int, metres: float, type_: str = "Grated OFK") -> Inlet:
    return Inlet(asset_number, type_, ORIGIN[0] + metres * DEG_PER_M, ORIGIN[1])


def test_distance_m_matches_known_offset():
    assert distance_m(*ORIGIN, ORIGIN[0] + 100 * DEG_PER_M, ORIGIN[1]) == pytest.approx(100, abs=0.5)


def test_nearest_inlet_ranks_first():
    inlets = [
        inlet_north_of_origin(1, 15),
        inlet_north_of_origin(2, 3),
        inlet_north_of_origin(3, 8),
    ]
    result = match(*ORIGIN, accuracy_m=10, source="gps", inlets=inlets)
    assert [c["asset_number"] for c in result] == [2, 3, 1]


def test_scores_sum_to_one_and_decrease_with_distance():
    inlets = [inlet_north_of_origin(i, i * 4) for i in range(1, 6)]
    result = match(*ORIGIN, accuracy_m=10, source="gps", inlets=inlets)
    assert sum(c["score"] for c in result) == pytest.approx(1, abs=1e-3)
    scores = [c["score"] for c in result]
    assert scores == sorted(scores, reverse=True)


def test_returns_at_most_top_n():
    inlets = [inlet_north_of_origin(i, i) for i in range(1, 11)]
    assert len(match(*ORIGIN, accuracy_m=10, source="gps", inlets=inlets)) == 5


def test_radius_grows_with_accuracy():
    inlet = [inlet_north_of_origin(1, 60)]
    assert match(*ORIGIN, accuracy_m=10, source="gps", inlets=inlet) == []
    assert len(match(*ORIGIN, accuracy_m=40, source="gps", inlets=inlet)) == 1
    assert search_radius(location_sigma(5, "gps")) == 20
    assert search_radius(location_sigma(500, "gps")) == 100


def test_manual_pin_uses_fixed_sigma():
    assert location_sigma(3, "manual") == MANUAL_SIGMA_M
    assert location_sigma(None, "gps") == 50


def test_empty_when_no_inlet_nearby():
    far = [inlet_north_of_origin(1, 500)]
    assert match(*ORIGIN, accuracy_m=10, source="gps", inlets=far) == []


def test_real_data_only_contains_inlet_types():
    path = Path(__file__).resolve().parent.parent / "app" / "data" / "inlets.geojson"
    inlets = load_inlets(path)
    assert len(inlets) > 1000
    excluded = {"Junction", "System Node", "Submerged", "Pipe to Pipe", "Not Known", "Other"}
    assert not {i.type for i in inlets} & excluded
    # A real Elizabeth St report should find candidates
    assert match(*ORIGIN, accuracy_m=15, source="gps", inlets=inlets)
