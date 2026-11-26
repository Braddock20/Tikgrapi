"""Tests for the browser control room.

They exercise the UI's own API — the same routes the browser calls — so a
regression in the front end cannot slip through unnoticed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

API_KEY = "test-ui-key"

HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture(scope="module")
def client():
    """The UI's own key, restored afterwards.

    The key is swapped only for this module: the rest of the suite uses the
    key from the environment.
    """
    original = settings.api_key
    settings.api_key = API_KEY
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        settings.api_key = original


# --------------------------------------------------------------------------
# the page itself
# --------------------------------------------------------------------------
def test_the_ui_is_served(client: TestClient) -> None:
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Control Room" in response.text


def test_the_root_redirects_to_the_ui(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302, 307)
    assert response.headers["location"].endswith("/ui/")


def test_the_static_files_are_served(client: TestClient) -> None:
    assert client.get("/ui/static/app.css").status_code == 200
    assert client.get("/ui/static/app.js").status_code == 200


# --------------------------------------------------------------------------
# the API behind the UI
# --------------------------------------------------------------------------
def test_the_api_needs_the_key(client: TestClient) -> None:
    assert client.get("/ui/api/status").status_code == 401
    assert client.get("/ui/api/status", headers={"X-API-Key": "wrong"}).status_code == 401


def test_the_key_may_travel_in_the_query(client: TestClient) -> None:
    """Media URLs (a poster, a download) cannot carry a header."""
    response = client.get(f"/ui/api/status?key={API_KEY}")
    assert response.status_code == 200


def test_status_reports_the_engine(client: TestClient) -> None:
    data = client.get("/ui/api/status", headers=HEADERS).json()
    assert "engine" in data and "content" in data and "assets" in data
    assert data["engine"]["profile"]["width"] == 1080
    assert data["content"]["items"] >= 0


def test_content_is_listed(client: TestClient) -> None:
    data = client.get("/ui/api/content", headers=HEADERS).json()
    assert "items" in data
    for item in data["items"][:5]:
        assert item["text"].strip()
        assert "category" in item and "used" in item


def test_files_are_listed_with_usable_urls(client: TestClient) -> None:
    data = client.get("/ui/api/files", headers=HEADERS).json()
    assert "files" in data
    for entry in data["files"][:5]:
        assert entry["url"].startswith("/ui/api/download/")


def test_only_known_commands_may_run(client: TestClient) -> None:
    bad = client.post("/ui/api/command", json={"command": "rm -rf /"}, headers=HEADERS)
    assert bad.status_code == 400
    unknown = client.post("/ui/api/command", json={"command": "python evil.py"}, headers=HEADERS)
    assert unknown.status_code == 400


def test_a_command_runs_and_reports(client: TestClient) -> None:
    started = client.post("/ui/api/command", json={"command": "status"}, headers=HEADERS)
    assert started.status_code == 200
    job_id = started.json()["id"]

    import time

    for _ in range(60):
        job = client.get(f"/ui/api/jobs/{job_id}", headers=HEADERS).json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] in {"completed", "failed"}
    assert job["exit_code"] == 0, job["lines"][-5:]


def test_an_upload_lands_in_the_right_folder(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/ui/api/upload",
        files={"file": ("my-photo.jpg", b"\xff\xd8\xff\xe0not-a-real-jpeg", "image/jpeg")},
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    stored = Path(body["stored"])
    assert stored.exists()
    assert stored.suffix == ".jpg"
    assert "photographs" in str(stored)
    stored.unlink()


def test_a_text_upload_goes_to_the_content_folder(client: TestClient) -> None:
    response = client.post(
        "/ui/api/upload",
        files={"file": ("ui_notes.json", b'[{"id":"x","text":"A line from the browser."}]', "application/json")},
        headers=HEADERS,
    )
    assert response.status_code == 200
    stored = Path(response.json()["stored"])
    assert stored.exists()
    assert stored.parent.name == "content"
    stored.unlink()


def test_an_unsupported_upload_is_refused(client: TestClient) -> None:
    response = client.post(
        "/ui/api/upload",
        files={"file": ("thing.exe", b"MZ", "application/octet-stream")},
        headers=HEADERS,
    )
    assert response.status_code == 400
