import pytest

from scripts.summary_report import (
    MAX_ZOOM,
    boxes_overlap,
    fit_zoom,
    lonlat_to_world,
    metres_per_pixel,
    nice_scale_length,
    place_labels,
)

# Elizabeth St / Collins St intersection
LAT, LON = -37.8136, 144.9631


def test_lonlat_to_world_origin_and_scale():
    assert lonlat_to_world(0, 0, 0) == pytest.approx((128, 128))
    x1, y1 = lonlat_to_world(LON, LAT, 10)
    x2, y2 = lonlat_to_world(LON, LAT, 11)
    assert (x2, y2) == pytest.approx((2 * x1, 2 * y1))


def test_metres_per_pixel_matches_projection():
    # 100 m north of the origin should be 100 m / (m per px) pixels up the image.
    zoom = 19
    _, y0 = lonlat_to_world(LON, LAT, zoom)
    _, y1 = lonlat_to_world(LON, LAT + 100 / 111_195, zoom)
    assert y0 - y1 == pytest.approx(100 / metres_per_pixel(LAT, zoom), rel=0.01)


def test_fit_zoom_fits_points_inside_padded_frame():
    points = [(LON, LAT), (LON + 0.004, LAT - 0.01)]
    width, height, padding = 800, 1200, 50
    zoom = fit_zoom(points, width, height, padding)
    xs, ys = zip(*(lonlat_to_world(lon, lat, zoom) for lon, lat in points))
    assert max(xs) - min(xs) <= width - 2 * padding + 1e-6
    assert max(ys) - min(ys) <= height - 2 * padding + 1e-6
    # And it is tight in at least one direction.
    assert max(max(xs) - min(xs), max(ys) - min(ys)) == pytest.approx(height - 2 * padding)


def test_fit_zoom_is_capped_for_a_single_point():
    assert fit_zoom([(LON, LAT)], 800, 600, 50) == MAX_ZOOM


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(7, 5), (10, 10), (19.9, 10), (20, 20), (49, 20), (51, 50), (260, 200), (0.3, 0.2)],
)
def test_nice_scale_length(limit, expected):
    assert nice_scale_length(limit) == pytest.approx(expected)


def test_place_labels_avoids_overlap_for_close_anchors():
    anchors = [(100, 100), (104, 100), (100, 104)]
    sizes = [(30, 20)] * 3
    positions = place_labels(anchors, sizes, offset=10)
    boxes = [(x, y, x + w, y + h) for (x, y), (w, h) in zip(positions, sizes)]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert not boxes_overlap(boxes[i], boxes[j])


def test_place_labels_prefers_right_when_free():
    assert place_labels([(100, 100)], [(30, 20)], offset=10) == [(110, 90)]


def test_place_labels_avoids_obstacles():
    obstacle = (105, 80, 200, 120)  # covers the right-hand slot
    (x, y), = place_labels([(100, 100)], [(30, 20)], offset=10, obstacles=[obstacle])
    assert not boxes_overlap((x, y, x + 30, y + 20), obstacle)
