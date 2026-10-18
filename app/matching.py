"""Match a report location to ranked candidate stormwater inlets.

Level 0 + Level 1 matching: take surface inlets within a search radius that
scales with location uncertainty, score each with a Gaussian on distance, and
normalise the scores across the returned candidates so they sum to 1.
"""

from dataclasses import dataclass
from pathlib import Path
import json
import math

EARTH_RADIUS_M = 6_371_000
MIN_SIGMA_M = 5.0
MAX_SIGMA_M = 50.0
MANUAL_SIGMA_M = 15.0
MIN_RADIUS_M = 20.0
TOP_N = 5


@dataclass(frozen=True)
class Inlet:
    asset_number: int
    type: str
    lat: float
    lon: float


def load_inlets(path: Path) -> list[Inlet]:
    with open(path) as f:
        collection = json.load(f)
    return [
        Inlet(
            asset_number=feat["properties"]["asset_number"],
            type=feat["properties"]["type"],
            lon=feat["geometry"]["coordinates"][0],
            lat=feat["geometry"]["coordinates"][1],
        )
        for feat in collection["features"]
    ]


def location_sigma(accuracy_m: float | None, source: str) -> float:
    """Standard deviation (m) of the location error used for scoring."""
    if source == "manual":
        return MANUAL_SIGMA_M
    if accuracy_m is None:
        return MAX_SIGMA_M
    return min(max(accuracy_m, MIN_SIGMA_M), MAX_SIGMA_M)


def search_radius(sigma: float) -> float:
    return max(MIN_RADIUS_M, 2 * sigma)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular approximation; accurate to well under 1 m at catchment scale."""
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(mean_lat) * EARTH_RADIUS_M
    dy = math.radians(lat2 - lat1) * EARTH_RADIUS_M
    return math.hypot(dx, dy)


def match(
    lat: float,
    lon: float,
    accuracy_m: float | None,
    source: str,
    inlets: list[Inlet],
    top_n: int = TOP_N,
) -> list[dict]:
    """Return up to top_n inlets near (lat, lon), best first, with scores summing to 1."""
    sigma = location_sigma(accuracy_m, source)
    radius = search_radius(sigma)

    nearby = []
    for inlet in inlets:
        d = distance_m(lat, lon, inlet.lat, inlet.lon)
        if d <= radius:
            nearby.append((d, inlet))
    nearby.sort(key=lambda pair: pair[0])
    nearby = nearby[:top_n]

    weights = [math.exp(-(d**2) / (2 * sigma**2)) for d, _ in nearby]
    total = sum(weights)
    return [
        {
            "asset_number": inlet.asset_number,
            "type": inlet.type,
            "lat": inlet.lat,
            "lon": inlet.lon,
            "distance_m": round(d, 1),
            "score": round(w / total, 4),
        }
        for (d, inlet), w in zip(nearby, weights)
    ]
