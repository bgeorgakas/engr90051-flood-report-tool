"""One-off data prep: build the inlet and catchment GeoJSON files used by the app.

Downloads the City of Melbourne stormwater pits and the Melbourne Water
"ELIZABETH ST DRAIN (CITY)" sub-catchment, clips the pits to the catchment,
keeps only surface inlets, and writes small GeoJSON files to app/data/.

Usage: python scripts/prepare_data.py
"""

from pathlib import Path
import json

import geopandas as gpd
import requests

PITS_URL = (
    "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "stormwater-pits/exports/geojson"
)
CATCHMENT_URL = (
    "https://services5.arcgis.com/ZSYwjtv8RKVhkXIL/arcgis/rest/services/"
    "Catchments_of_all_Waterways_and_Drains/FeatureServer/1/query"
)
CATCHMENT_NBR = "4401"  # ELIZABETH ST DRAIN (CITY)

# Pit types where surface water can enter the network. Everything else
# (Junction, System Node, Submerged, Pipe to Pipe, Not Known, ...) is excluded.
INLET_TYPES = {
    "Grated OFK",
    "Double Grated OFK",
    "Grated Kerbside",
    "Side Entry",
    "Grated Side Entry",
    "Double GSEP",
    "Lane Type",
    "Grated Manhole",
    "Trench Grate",
    "Run Through Inlet",
    "Head Wall Inlet",
}

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data"


def fetch_catchment() -> gpd.GeoDataFrame:
    params = {
        "where": f"SUB_CATCHMENT_NBR = '{CATCHMENT_NBR}'",
        "outFields": "SUB_CATCHMENT_NBR,SUB_CATCHMENT_NAME,AREA_SQ_KM",
        "outSR": "4326",
        "f": "geojson",
    }
    resp = requests.get(CATCHMENT_URL, params=params, timeout=60)
    resp.raise_for_status()
    catchment = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
    if len(catchment) != 1:
        raise SystemExit(f"Expected 1 catchment polygon, got {len(catchment)}")
    return catchment


def fetch_pits() -> gpd.GeoDataFrame:
    resp = requests.get(PITS_URL, timeout=300)
    resp.raise_for_status()
    return gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")


def main() -> None:
    catchment = fetch_catchment()
    print(f"Catchment: {catchment.iloc[0]['SUB_CATCHMENT_NAME']} "
          f"({catchment.iloc[0]['AREA_SQ_KM']} km2)")

    pits = fetch_pits()
    print(f"Pits (all of CoM): {len(pits)}")

    in_catchment = gpd.clip(pits, catchment)
    print(f"Pits in catchment: {len(in_catchment)}")
    print("Pit types in catchment (* = kept as inlet):")
    counts = in_catchment["object_type_lupvalue"].value_counts(dropna=False)
    for pit_type, n in counts.items():
        mark = "*" if pit_type in INLET_TYPES else " "
        print(f"  {mark} {pit_type}: {n}")

    inlets = in_catchment[in_catchment["object_type_lupvalue"].isin(INLET_TYPES)]
    print(f"Inlets written: {len(inlets)}")

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(row.geometry.x, 7), round(row.geometry.y, 7)],
            },
            "properties": {
                "asset_number": int(row["asset_number"]),
                "type": row["object_type_lupvalue"],
            },
        }
        for _, row in inlets.iterrows()
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "inlets.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    catchment.to_file(OUT_DIR / "catchment.geojson", driver="GeoJSON")
    print(f"Wrote {OUT_DIR / 'inlets.geojson'} and {OUT_DIR / 'catchment.geojson'}")


if __name__ == "__main__":
    main()
