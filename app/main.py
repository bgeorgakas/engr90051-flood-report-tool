"""FastAPI app: report submission API, read-only report API, and the static frontend."""

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db
from app.matching import load_inlets, match

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_NOTE_CHARS = 500

inlets = load_inlets(DATA_DIR / "inlets.geojson")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Elizabeth St Flood Reports", lifespan=lifespan)


@app.post("/api/reports", status_code=201)
def create_report(
    photo: Annotated[UploadFile, File()],
    lat: Annotated[float, Form(ge=-90, le=90)],
    lon: Annotated[float, Form(ge=-180, le=180)],
    location_source: Annotated[Literal["gps", "manual"], Form()],
    accuracy_m: Annotated[float | None, Form(ge=0)] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
    note: Annotated[str | None, Form(max_length=MAX_NOTE_CHARS)] = None,
):
    if not (photo.content_type or "").startswith("image/"):
        raise HTTPException(400, "Photo must be an image")
    content = photo.file.read(MAX_PHOTO_BYTES + 1)
    if len(content) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is too large")

    candidates = match(lat, lon, accuracy_m, location_source, inlets)
    report_id = db.insert_report(
        captured_at=captured_at,
        lat=lat,
        lon=lon,
        accuracy_m=accuracy_m,
        location_source=location_source,
        photo=content,
        photo_mime=photo.content_type,
        candidates=candidates,
        note=(note or "").strip() or None,
    )
    return {"id": report_id}


@app.get("/api/reports")
def get_reports():
    return db.list_reports()


@app.delete("/api/reports")
def delete_reports():
    """Delete all reports. Unauthenticated: acceptable for the prototype only."""
    return {"deleted": db.clear_reports()}


@app.get("/api/reports/{report_id}/photo")
def get_report_photo(report_id: int):
    row = db.get_photo(report_id)
    if row is None:
        raise HTTPException(404, "Report not found")
    return Response(content=bytes(row["photo"]), media_type=row["photo_mime"])


@app.get("/api/inlets")
def get_inlets():
    return FileResponse(DATA_DIR / "inlets.geojson", media_type="application/geo+json")


@app.get("/api/catchment")
def get_catchment():
    return FileResponse(DATA_DIR / "catchment.geojson", media_type="application/geo+json")


app.mount("/", StaticFiles(directory=APP_DIR / "static", html=True), name="static")
