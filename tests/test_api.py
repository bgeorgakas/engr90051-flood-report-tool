from fastapi.testclient import TestClient

from app import main

# Not used as a context manager, so the lifespan (database setup) doesn't run.
client = TestClient(main.app)


REPORT_FORM = {"lat": "-37.8136", "lon": "144.9631", "location_source": "gps", "accuracy_m": "10"}
PHOTO = {"photo": ("photo.jpg", b"\xff\xd8fake\xff\xd9", "image/jpeg")}


def capture_insert(monkeypatch) -> dict:
    saved = {}

    def fake_insert(**kwargs):
        saved.update(kwargs)
        return 1

    monkeypatch.setattr(main.db, "insert_report", fake_insert)
    return saved


def test_note_is_trimmed_and_stored(monkeypatch):
    saved = capture_insert(monkeypatch)
    resp = client.post("/api/reports", data={**REPORT_FORM, "note": "  grate blocked  "}, files=PHOTO)
    assert resp.status_code == 201
    assert saved["note"] == "grate blocked"


def test_missing_or_blank_note_stored_as_null(monkeypatch):
    saved = capture_insert(monkeypatch)
    client.post("/api/reports", data=REPORT_FORM, files=PHOTO)
    assert saved["note"] is None
    client.post("/api/reports", data={**REPORT_FORM, "note": "   "}, files=PHOTO)
    assert saved["note"] is None


def test_note_too_long_rejected(monkeypatch):
    capture_insert(monkeypatch)
    resp = client.post("/api/reports", data={**REPORT_FORM, "note": "x" * 501}, files=PHOTO)
    assert resp.status_code == 422


def test_clear_reports(monkeypatch):
    monkeypatch.setattr(main.db, "clear_reports", lambda: 7)
    resp = client.delete("/api/reports")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 7}
