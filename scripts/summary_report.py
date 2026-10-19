"""Build a PDF summary of every report, for grading the matching by hand.

The cover page has an overview map, a blank tally table and the grade
definitions. Each report then gets one page: photo, zoomed map of the report
location and its candidate inlets, the candidate table, and fillable
"Correct / Almost correct / Not correct" boxes plus an "Actual asset no." field.

Uses DATABASE_URL from the environment, or from .env in the project root.
Map tiles come from tile.openstreetmap.org and are cached in
~/.cache/flood-report-tool/tiles.

Usage: python scripts/summary_report.py [-o OUTPUT.pdf]
"""

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json
import math
import sys

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app import db  # noqa: E402
from app.matching import load_inlets, location_sigma, search_radius  # noqa: E402

DATA_DIR = ROOT / "app" / "data"
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_CACHE = Path.home() / ".cache" / "flood-report-tool" / "tiles"
USER_AGENT = "engr90051-flood-report-tool/1.0 (summary report script)"
TILE_SIZE = 256
MAX_TILE_ZOOM = 19
# Allow up to 2x upscaling of z19 tiles so small search radii still fill the frame.
MAX_ZOOM = 20.0
# Zoomed report maps always show at least this far (m) around the report point.
MIN_FRAME_HALF_M = 25.0
LOCAL_TZ = ZoneInfo("Australia/Melbourne")

GRADES = [("correct", "Correct"), ("almost", "Almost correct"), ("not", "Not correct")]
ATTRIBUTION = (
    "Basemap © OpenStreetMap contributors. Stormwater pits: City of Melbourne (CC BY 4.0). "
    "Catchment: Melbourne Water (CC BY 4.0)."
)

# Map colours, matching app/static/map.js.
REPORT_FILL = (59, 130, 246)
REPORT_EDGE = (29, 78, 216)
NO_CANDIDATES_FILL = (220, 38, 38)
TOP_CANDIDATE = (194, 65, 12)
CANDIDATE = (249, 115, 22)
OTHER_INLET = (100, 116, 139)
CATCHMENT = (37, 99, 235)


# ---------------------------------------------------------------------------
# Web Mercator maps drawn onto OSM tiles
# ---------------------------------------------------------------------------

def lonlat_to_world(lon: float, lat: float, zoom: float) -> tuple[float, float]:
    """Web Mercator pixel coordinates at a (possibly fractional) zoom level."""
    scale = TILE_SIZE * 2**zoom
    x = (lon + 180) / 360 * scale
    lat_r = math.radians(lat)
    y = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * scale
    return x, y


def metres_per_pixel(lat: float, zoom: float) -> float:
    return 2 * math.pi * 6_378_137 * math.cos(math.radians(lat)) / (TILE_SIZE * 2**zoom)


def fit_zoom(points: list[tuple[float, float]], width: int, height: int, padding: int) -> float:
    """Largest zoom (capped at MAX_ZOOM) at which all (lon, lat) points fit inside the frame."""
    xs, ys = zip(*(lonlat_to_world(lon, lat, 0) for lon, lat in points))
    span_x = max(max(xs) - min(xs), 1e-12)
    span_y = max(max(ys) - min(ys), 1e-12)
    zoom = min(
        math.log2((width - 2 * padding) / span_x),
        math.log2((height - 2 * padding) / span_y),
    )
    return min(zoom, MAX_ZOOM)


def nice_scale_length(max_metres: float) -> float:
    """Largest 1/2/5 x 10^n length that is no longer than max_metres."""
    exponent = math.floor(math.log10(max_metres))
    for step in (5, 2, 1):
        if step * 10**exponent <= max_metres:
            return step * 10**exponent
    return 10 ** (exponent - 1) * 5


def fetch_tile(z: int, x: int, y: int, session: requests.Session) -> Image.Image:
    path = TILE_CACHE / str(z) / str(x) / f"{y}.png"
    if not path.exists():
        resp = session.get(TILE_URL.format(z=z, x=x, y=y), timeout=30)
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    return Image.open(path).convert("RGB")


@dataclass
class MapFrame:
    """A rendered basemap plus the projection needed to draw on it."""

    image: Image.Image
    zoom: float
    origin: tuple[float, float]  # world pixel at the image's top-left corner
    centre_lat: float

    def px(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = lonlat_to_world(lon, lat, self.zoom)
        return x - self.origin[0], y - self.origin[1]

    def metres_to_px(self, metres: float) -> float:
        return metres / metres_per_pixel(self.centre_lat, self.zoom)


def render_basemap(
    centre: tuple[float, float], zoom: float, width: int, height: int, session: requests.Session
) -> MapFrame:
    """Compose OSM tiles around (lon, lat) centre into a width x height image at a fractional zoom."""
    tile_zoom = min(MAX_TILE_ZOOM, math.ceil(zoom))
    factor = 2 ** (tile_zoom - zoom)  # tile-zoom pixels per output pixel
    cx, cy = lonlat_to_world(*centre, tile_zoom)
    left, top = cx - width * factor / 2, cy - height * factor / 2
    right, bottom = cx + width * factor / 2, cy + height * factor / 2

    tx0, ty0 = int(left // TILE_SIZE), int(top // TILE_SIZE)
    tx1, ty1 = int(right // TILE_SIZE), int(bottom // TILE_SIZE)
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * TILE_SIZE, (ty1 - ty0 + 1) * TILE_SIZE))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            tile = fetch_tile(tile_zoom, tx, ty, session)
            mosaic.paste(tile, ((tx - tx0) * TILE_SIZE, (ty - ty0) * TILE_SIZE))

    crop = (left - tx0 * TILE_SIZE, top - ty0 * TILE_SIZE, right - tx0 * TILE_SIZE, bottom - ty0 * TILE_SIZE)
    image = mosaic.resize((width, height), Image.Resampling.LANCZOS, box=crop)
    ox, oy = lonlat_to_world(*centre, zoom)
    return MapFrame(image, zoom, (ox - width / 2, oy - height / 2), centre[1])


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def dashed_line(draw: ImageDraw.ImageDraw, a, b, fill, width: int, dash: float, gap: float) -> None:
    length = math.dist(a, b)
    if length == 0:
        return
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        draw.line([(a[0] + ux * pos, a[1] + uy * pos), (a[0] + ux * end, a[1] + uy * end)], fill=fill, width=width)
        pos = end + gap


def dot(draw: ImageDraw.ImageDraw, xy, radius: float, fill, outline, width: int) -> None:
    x, y = xy
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=outline, width=width)


def boxes_overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place_labels(anchors, sizes, offset: float, obstacles=()) -> list[tuple[float, float]]:
    """Greedily choose a top-left position next to each anchor so labels don't overlap.

    anchors: (x, y) points; sizes: (w, h) of each label; obstacles: boxes to avoid.
    Tries eight positions around the anchor and keeps the first free one (or the
    one with fewest overlaps if none are free).
    """
    placed: list[tuple[float, float, float, float]] = []
    positions = []
    for (x, y), (w, h) in zip(anchors, sizes):
        options = [
            (x + offset, y - h / 2),          # right
            (x - offset - w, y - h / 2),      # left
            (x - w / 2, y - offset - h),      # above
            (x - w / 2, y + offset),          # below
            (x + offset, y - offset - h),     # above right
            (x - offset - w, y - offset - h), # above left
            (x + offset, y + offset),         # below right
            (x - offset - w, y + offset),     # below left
        ]

        def clashes(pos):
            box = (pos[0], pos[1], pos[0] + w, pos[1] + h)
            return sum(boxes_overlap(box, other) for other in [*placed, *obstacles])

        best = min(options, key=clashes)  # min() keeps the first option among ties
        placed.append((best[0], best[1], best[0] + w, best[1] + h))
        positions.append(best)
    return positions


def draw_label(draw: ImageDraw.ImageDraw, xy, text: str, fnt, fill=(15, 23, 42)) -> None:
    draw.text(xy, text, font=fnt, fill=fill, stroke_width=3, stroke_fill="white")


def draw_scale_and_attribution(image: Image.Image, frame: MapFrame) -> None:
    draw = ImageDraw.Draw(image)
    w, h = image.size
    fnt = font(max(14, w // 55))

    metres = nice_scale_length(metres_per_pixel(frame.centre_lat, frame.zoom) * w / 4)
    bar = frame.metres_to_px(metres)
    x0, y0 = 20, h - 30
    label = f"{metres:g} m"
    lw = draw.textlength(label, font=fnt)
    draw.rectangle((x0 - 8, y0 - fnt.size - 16, x0 + max(bar, lw) + 8, y0 + 12), fill="white")
    draw.line([(x0, y0), (x0 + bar, y0)], fill="black", width=4)
    for x in (x0, x0 + bar):
        draw.line([(x, y0 - 8), (x, y0 + 4)], fill="black", width=3)
    draw.text((x0, y0 - fnt.size - 12), label, font=fnt, fill="black")

    small = font(max(12, w // 75))
    text = "© OpenStreetMap contributors"
    tw = draw.textlength(text, font=small)
    draw.rectangle((w - tw - 12, h - small.size - 10, w, h), fill="white")
    draw.text((w - tw - 6, h - small.size - 6), text, font=small, fill=(51, 65, 85))


def faded(image: Image.Image, amount: float = 0.35) -> Image.Image:
    """Blend the basemap towards white so the overlays stand out."""
    return Image.blend(image, Image.new("RGB", image.size, "white"), amount).convert("RGBA")


def report_map(report: dict, inlets, session, width: int, height: int) -> Image.Image:
    """Zoomed map: report point, search radius, candidates #1-#5 and other nearby inlets."""
    radius_m = search_radius(location_sigma(report["accuracy_m"], report["location_source"]))
    lat, lon = report["lat"], report["lon"]
    # Frame every candidate plus at least MIN_FRAME_HALF_M around the report point. A large
    # search circle may run off the edge; zooming out to show it would merge nearby pits.
    half = min(radius_m, MIN_FRAME_HALF_M)
    dlat = half / 111_195
    dlon = dlat / math.cos(math.radians(lat))
    points = [(lon - dlon, lat - dlat), (lon + dlon, lat + dlat)]
    points += [(c["lon"], c["lat"]) for c in report["candidates"]]
    zoom = fit_zoom(points, width, height, padding=width // 10)

    frame = render_basemap((lon, lat), zoom, width, height, session)
    image = faded(frame.image)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    unit = width / 1000  # scale marks with the image size

    centre = frame.px(lon, lat)
    r = frame.metres_to_px(radius_m)
    draw.ellipse((centre[0] - r, centre[1] - r, centre[0] + r, centre[1] + r),
                 fill=(*REPORT_FILL, 30), outline=(*REPORT_EDGE, 220), width=round(4 * unit))

    candidate_ids = {c["asset_number"] for c in report["candidates"]}
    for inlet in inlets:
        if inlet.asset_number in candidate_ids:
            continue
        x, y = frame.px(inlet.lon, inlet.lat)
        if -20 <= x <= width + 20 and -20 <= y <= height + 20:
            dot(draw, (x, y), 10 * unit, (*OTHER_INLET, 235), (255, 255, 255, 255), round(3 * unit))

    cand_px = [frame.px(c["lon"], c["lat"]) for c in report["candidates"]]
    cand_r = [(20 if i == 0 else 15) * unit for i in range(len(cand_px))]
    for xy in cand_px:
        dashed_line(draw, centre, xy, (*CANDIDATE, 255), round(4 * unit), 12 * unit, 8 * unit)
    for i, xy in reversed(list(enumerate(cand_px))):  # draw #1 last, on top
        colour = TOP_CANDIDATE if i == 0 else CANDIDATE
        dot(draw, xy, cand_r[i], (*colour, 240), (255, 255, 255, 255), round(4 * unit))

    report_r = 15 * unit
    dot(draw, centre, report_r, (*REPORT_FILL, 255), (255, 255, 255, 255), round(4 * unit))
    image = Image.alpha_composite(image, overlay)

    draw = ImageDraw.Draw(image)
    fnt = font(round(40 * unit))
    labels = [f"#{i + 1}" for i in range(len(cand_px))]
    sizes = [(draw.textlength(t, font=fnt), fnt.size) for t in labels]
    obstacles = [(x - rr, y - rr, x + rr, y + rr) for (x, y), rr in zip([centre, *cand_px], [report_r, *cand_r])]
    for i, xy in enumerate(place_labels(cand_px, sizes, 24 * unit, obstacles)):
        draw_label(draw, xy, labels[i], fnt, fill=TOP_CANDIDATE if i == 0 else (124, 45, 18))

    draw_scale_and_attribution(image, frame)
    return image.convert("RGB")


def overview_map(reports: list[dict], session, width: int, height: int) -> Image.Image:
    """All reports on one map, numbered by report ID, with the catchment outline."""
    points = [(r["lon"], r["lat"]) for r in reports]
    zoom = fit_zoom(points, width, height, padding=width // 8)
    lons, lats = zip(*points)
    centre = ((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)
    frame = render_basemap(centre, zoom, width, height, session)
    image = faded(frame.image)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    unit = width / 1000

    with open(DATA_DIR / "catchment.geojson") as f:
        catchment = json.load(f)
    for feature in catchment["features"]:
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for polygon in polygons:
            ring = [frame.px(lon, lat) for lon, lat in polygon[0]]
            for a, b in zip(ring, ring[1:]):
                dashed_line(draw, a, b, (*CATCHMENT, 220), round(5 * unit), 16 * unit, 10 * unit)

    marker_r = 13 * unit
    marker_px = [frame.px(r["lon"], r["lat"]) for r in reports]
    for r, xy in zip(reports, marker_px):
        fill = REPORT_FILL if r["candidates"] else NO_CANDIDATES_FILL
        dot(draw, xy, marker_r, (*fill, 245), (255, 255, 255, 255), round(4 * unit))
    image = Image.alpha_composite(image, overlay)

    draw = ImageDraw.Draw(image)
    fnt = font(round(42 * unit))
    labels = [str(r["id"]) for r in reports]
    sizes = [(draw.textlength(t, font=fnt), fnt.size) for t in labels]
    obstacles = [(x - marker_r, y - marker_r, x + marker_r, y + marker_r) for x, y in marker_px]
    for xy, text in zip(place_labels(marker_px, sizes, 18 * unit, obstacles), labels):
        draw_label(draw, xy, text, fnt)

    draw_scale_and_attribution(image, frame)
    return image.convert("RGB")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
CONTENT_W = PAGE_W - 2 * MARGIN
STYLES = getSampleStyleSheet()
BODY = STYLES["BodyText"]
SMALL = STYLES["BodyText"].clone("Small", fontSize=8, leading=10, textColor=colors.HexColor("#475569"))


def jpeg_reader(image: Image.Image, max_side: int | None = None, quality: int = 85) -> ImageReader:
    if max_side and max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return ImageReader(buf)


def draw_image_fit(c: Canvas, reader: ImageReader, x: float, y: float, w: float, h: float) -> None:
    """Draw an image as large as possible inside the box, centred, with a thin border."""
    iw, ih = reader.getSize()
    scale = min(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    dx, dy = x + (w - dw) / 2, y + (h - dh) / 2
    c.drawImage(reader, dx, dy, dw, dh)
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(0.5)
    c.rect(dx, dy, dw, dh)


def draw_paragraph(c: Canvas, text: str, style, x: float, top: float, width: float) -> float:
    """Draw a paragraph with its top edge at `top`; return the y of its bottom edge."""
    p = Paragraph(text, style)
    _, h = p.wrap(width, PAGE_H)
    p.drawOn(c, x, top - h)
    return top - h


def report_time(report: dict) -> datetime:
    return (report["captured_at"] or report["received_at"]).astimezone(LOCAL_TZ)


def location_text(report: dict) -> str:
    if report["location_source"] == "gps":
        return f"GPS ±{report['accuracy_m']:.0f} m"
    return "Pin placed manually"


def candidate_table(report: dict) -> Table:
    header = ["#", "Asset no.", "Type", "Distance", "Score"]
    rows = [
        [str(i + 1), str(c["asset_number"]), c["type"], f"{c['distance_m']:.1f} m", f"{c['score'] * 100:.0f}%"]
        for i, c in enumerate(report["candidates"])
    ] or [["", "No inlets within search radius", "", "", ""]]
    table = Table([header, *rows], colWidths=[10 * mm, 30 * mm, 70 * mm, 30 * mm, 25 * mm])
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#334155")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if report["candidates"]:
        style.append(("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 9))
        style.append(("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#ffedd5")))
    else:
        style.append(("SPAN", (1, 1), (-1, 1)))
    table.setStyle(TableStyle(style))
    return table


def grade_box(c: Canvas, report_id: int, x: float, top: float, width: float) -> None:
    height = 30 * mm
    y = top - height
    c.setStrokeColor(colors.HexColor("#334155"))
    c.setLineWidth(0.75)
    c.rect(x, y, width, height)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + 4 * mm, top - 6 * mm, "Grade: was the #1 candidate the drain that was photographed?")

    box = 5 * mm
    bx = x + 4 * mm
    by = top - 16 * mm
    c.setFont("Helvetica", 10)
    for value, label in GRADES:
        # A radio group gives mutually exclusive boxes; "radio" alone lets a box be unticked again.
        c.acroForm.radio(
            name=f"report_{report_id}_grade", value=value, selected=False,
            buttonStyle="check", shape="square", size=box, x=bx, y=by,
            borderColor=colors.black, fillColor=colors.white, borderWidth=1,
            fieldFlags="radio", tooltip=f"Report {report_id}: {label}",
        )
        c.drawString(bx + box + 2 * mm, by + 1.5 * mm, label)
        bx += box + 2 * mm + c.stringWidth(label, "Helvetica", 10) + 10 * mm

    c.drawString(x + 4 * mm, y + 5 * mm, "Actual asset no.:")
    fx = x + 4 * mm + c.stringWidth("Actual asset no.:", "Helvetica", 10) + 2 * mm
    c.acroForm.textfield(
        name=f"report_{report_id}_actual_asset", x=fx, y=y + 3 * mm, width=45 * mm, height=7 * mm,
        borderColor=colors.HexColor("#334155"), fillColor=colors.white, borderWidth=0.75,
        fontName="Helvetica", fontSize=10, maxlen=20, tooltip=f"Report {report_id}: actual asset number",
    )


def footer(c: Canvas, text: str) -> None:
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(MARGIN, 8 * mm, text)
    c.drawRightString(PAGE_W - MARGIN, 8 * mm, f"Page {c.getPageNumber()}")
    c.setFillColor(colors.black)


def draw_cover(c: Canvas, reports: list[dict], session) -> None:
    dates = sorted({report_time(r).date() for r in reports})
    survey = ", ".join(d.strftime("%-d %B %Y") for d in dates) or "–"
    top = PAGE_H - MARGIN

    c.setFont("Helvetica-Bold", 18)
    c.drawString(MARGIN, top - 18, "Elizabeth St flood report field test: matching review")
    top = draw_paragraph(
        c,
        f"<b>Survey date:</b> {survey} &nbsp; &nbsp; <b>Reports:</b> {len(reports)} &nbsp; &nbsp; "
        f"<b>Generated:</b> {date.today().strftime('%-d %B %Y')}",
        BODY, MARGIN, top - 26, CONTENT_W,
    )

    # Left column: overview map. Right column: tally, grade definitions, method.
    top -= 5 * mm
    map_w, map_h = 95 * mm, 205 * mm
    image = overview_map(reports, session, 1000, round(1000 * map_h / map_w))
    draw_image_fit(c, jpeg_reader(image, quality=88), MARGIN, top - map_h, map_w, map_h)
    caption_bottom = draw_paragraph(
        c,
        "Report locations, labelled with report numbers (one page each). Blue: candidates found; "
        "red: no inlet within the search radius. Dashed blue line: Elizabeth St Drain catchment.",
        SMALL, MARGIN, top - map_h - 2 * mm, map_w,
    )

    rx = MARGIN + map_w + 8 * mm
    rw = CONTENT_W - map_w - 8 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(rx, top - 12, "Tally")
    rows = ["Correct", "Almost correct", "Not correct", "No candidates", "Total"]
    row_h = 9 * mm
    ty = top - 20
    c.setLineWidth(0.5)
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    for i, label in enumerate(rows):
        y = ty - (i + 1) * row_h
        c.setFont("Helvetica-Bold" if label == "Total" else "Helvetica", 10)
        c.drawString(rx, y + 3 * mm, label)
        if label == "No candidates":
            # Known up front, so print it rather than leaving it to fill in.
            c.drawString(rx + rw - 25 * mm + 2 * mm, y + 3 * mm,
                         str(sum(not r["candidates"] for r in reports)))
        else:
            c.acroForm.textfield(
                name=f"tally_{label.lower().replace(' ', '_')}", x=rx + rw - 25 * mm, y=y + 1.5 * mm,
                width=25 * mm, height=6.5 * mm, borderColor=colors.HexColor("#334155"),
                fillColor=colors.white, borderWidth=0.75, fontName="Helvetica", fontSize=10, maxlen=6,
                tooltip=f"Number of reports: {label}",
            )
        c.line(rx, y, rx + rw, y)

    y = ty - len(rows) * row_h - 8 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(rx, y, "Grade definitions")
    y = draw_paragraph(
        c,
        "Grade only the <b>#1 (top-ranked) candidate</b> against the drain that was photographed.<br/><br/>"
        "<b>Correct:</b> the #1 candidate is the drain that was photographed.<br/>"
        "<b>Almost correct:</b> the photographed drain is candidate #2 to #5.<br/>"
        "<b>Not correct:</b> the photographed drain is not among the candidates. A pit next to "
        "the photographed one does <b>not</b> count as a match.<br/>"
        "<b>No candidates:</b> no inlet within the search radius (counted separately).<br/><br/>"
        "Where the grade is not Correct, write the asset number of the drain actually "
        "photographed in <i>Actual asset no.</i>",
        BODY, rx, y - 4, rw,
    )

    y -= 6 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(rx, y, "How matching works")
    draw_paragraph(
        c,
        "Only surface inlets are candidates. Location uncertainty (sigma) is the GPS accuracy clamped "
        "to 5 to 50 m (a manual pin uses 15 m). Search radius = max(20 m, 2 sigma), shown as the blue "
        "circle on each map. Each inlet in the radius is scored exp(-d²/2sigma²); the top 5 are "
        "listed with scores normalised to sum to 100%.",
        BODY, rx, y - 4, rw,
    )

    draw_paragraph(c, ATTRIBUTION, SMALL, MARGIN, caption_bottom - 6 * mm, CONTENT_W)
    footer(c, "Elizabeth St flood report field test")


def draw_report_page(c: Canvas, report: dict, photo: dict | None, inlets, session) -> None:
    top = PAGE_H - MARGIN
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN, top - 16, f"Report #{report['id']}")
    details = (
        f"{report_time(report).strftime('%-d %b %Y, %-I:%M:%S %p')} &nbsp;·&nbsp; {location_text(report)}"
        f" &nbsp;·&nbsp; {report['lat']:.6f}, {report['lon']:.6f}"
    )
    if report.get("note"):
        details += f"<br/><b>Note:</b> {report['note']}"
    top = draw_paragraph(c, details, BODY, MARGIN, top - 22, CONTENT_W)

    top -= 4 * mm
    gap = 6 * mm
    box_w = (CONTENT_W - gap) / 2
    box_h = 120 * mm
    if photo is not None:
        img = ImageOps.exif_transpose(Image.open(BytesIO(bytes(photo["photo"]))))
        draw_image_fit(c, jpeg_reader(img, max_side=1600), MARGIN, top - box_h, box_w, box_h)
    map_px_w = 900
    map_img = report_map(report, inlets, session, map_px_w, round(map_px_w * box_h / box_w))
    draw_image_fit(c, jpeg_reader(map_img, quality=88), MARGIN + box_w + gap, top - box_h, box_w, box_h)
    top -= box_h
    top = draw_paragraph(
        c,
        "Left: submitted photo. Right: report location (blue dot) and search radius (blue circle); "
        "candidates #1 to #5 in orange (#1 darker and larger); other surface inlets in grey.",
        SMALL, MARGIN, top - 2 * mm, CONTENT_W,
    )

    top -= 5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN, top - 11, "Candidate inlets")
    table = candidate_table(report)
    _, th = table.wrap(CONTENT_W, PAGE_H)
    table.drawOn(c, MARGIN, top - 16 - th)
    top -= 16 + th

    grade_box(c, report["id"], MARGIN, top - 7 * mm, CONTENT_W)
    footer(c, f"Report #{report['id']}")


def build_pdf(output: Path) -> int:
    reports = sorted(db.list_reports(), key=lambda r: r["id"])
    if not reports:
        sys.exit("No reports in the database.")
    inlets = load_inlets(DATA_DIR / "inlets.geojson")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    c = Canvas(str(output), pagesize=A4)
    c.setTitle("Elizabeth St flood report field test: matching review")
    draw_cover(c, reports, session)
    c.showPage()
    for report in reports:
        print(f"Report #{report['id']}…", file=sys.stderr)
        draw_report_page(c, report, db.get_photo(report["id"]), inlets, session)
        c.showPage()
    c.save()
    return len(reports)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-o", "--output", type=Path,
        default=Path(f"reports-summary-{date.today():%Y%m%d}.pdf"),
        help="output PDF path (default: reports-summary-YYYYMMDD.pdf)",
    )
    args = parser.parse_args()
    count = build_pdf(args.output)
    print(f"Wrote {args.output} ({count} reports, {count + 1} pages).")


if __name__ == "__main__":
    main()
