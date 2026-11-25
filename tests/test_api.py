"""API behaviour: auth, job lifecycle, operator switches, session handling."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

HEADERS = {"X-API-Key": settings.api_key}


def client() -> TestClient:
    return TestClient(app)


def test_health_is_public_and_reports_the_session():
    with client() as c:
        r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "session" in body
    assert body["session"].get("status") in ("missing", "unverified", "valid", "invalid")


def test_jobs_require_the_api_key():
    with client() as c:
        assert c.get("/jobs").status_code == 401
        assert c.post("/jobs", json={"operation": "me"}).status_code == 401


def test_a_bearer_token_also_works():
    with client() as c:
        r = c.get("/jobs", headers={"Authorization": f"Bearer {settings.api_key}"})
    assert r.status_code == 200


def test_unknown_operations_are_rejected():
    with client() as c:
        r = c.post("/jobs", json={"operation": "definitely_not_real"}, headers=HEADERS)
    assert r.status_code == 422  # rejected by the schema validator


def test_write_operations_are_refused_while_they_are_disabled():
    with client() as c:
        r = c.post(
            "/jobs",
            json={"operation": "like", "params": {"video_id": "1"}},
            headers=HEADERS,
        )
    assert r.status_code == 403
    assert "ALLOW_WRITE_OPS" in r.json()["detail"]


def test_missing_parameters_are_reported():
    with client() as c:
        r = c.post("/jobs", json={"operation": "comment", "params": {}}, headers=HEADERS)
    assert r.status_code == 400
    assert "video_id" in r.json()["detail"]


def test_a_job_is_accepted_and_reaches_a_terminal_state():
    with client() as c:
        created = c.post("/jobs", json={"operation": "me"}, headers=HEADERS)
        assert created.status_code == 200
        job_id = created.json()["job_id"]

        # the inline queue runs it; without a session the job fails, but the
        # lifecycle must still complete
        for _ in range(50):
            job = c.get(f"/jobs/{job_id}", headers=HEADERS).json()
            if job["status"] not in ("queued", "running"):
                break
            import time

            time.sleep(0.1)
    assert job["status"] in ("failed", "completed")
    assert job["attempts"] == 1


def test_idempotency_keys_deduplicate():
    key = "dedupe-test-key"
    with client() as c:
        first = c.post("/jobs", json={"operation": "me", "idempotency_key": key}, headers=HEADERS)
        second = c.post("/jobs", json={"operation": "me", "idempotency_key": key}, headers=HEADERS)
    assert first.status_code == 200
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json().get("deduplicated") is True


def test_pause_and_resume_toggle_the_write_switch():
    with client() as c:
        assert c.post("/admin/pause", headers=HEADERS).json() == {"writes_paused": True}
        assert c.get("/health").json()["writes_paused"] is True
        assert c.post("/admin/resume", headers=HEADERS).json() == {"writes_paused": False}
        assert c.get("/health").json()["writes_paused"] is False


def test_the_operations_catalogue_is_exposed():
    with client() as c:
        r = c.get("/api/operations", headers=HEADERS)
    body = r.json()
    names = {o["operation"] for o in body["operations"]}
    assert {"me", "profile", "comments", "followers", "following"} <= names
    assert "like" in body["write_operations"]


def test_uploading_a_cookie_header_extracts_the_sessionid():
    cookies = (
        "sessionid=a1b2c3d4e5f60718293a4b5c6d7e8f90; "
        "sessionid_ss=a1b2c3d4e5f60718293a4b5c6d7e8f90; "
        "ttwid=1%7Cabc%7C1%7Cdef; msToken=x-token; "
        "s_v_web_id=verify_mwwjerri_0iYlonMU_Tril_4DCp; "
        "odin_tt=deadbeef; tt_csrf_token=csrf; "
        "tt-target-idc=alisg; store-country-code=ke"
    )
    with client() as c:
        r = c.post("/admin/session", content=cookies, headers={**HEADERS, "Content-Type": "text/plain"})
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is True and body["encrypted"] is True
    assert body["sessionid"] == "a1b2c3…8f90"
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" not in json.dumps(body)

    meta = c.get("/admin/session", headers=HEADERS).json()
    assert meta["present"] is True
    assert meta["session"]["sessionid"] == "a1b2c3…8f90"

def test_uploading_a_session_without_a_sessionid_is_rejected():
    with client() as c:
        r = c.post(
            "/admin/session",
            content="msToken=x; ttwid=y",
            headers={**HEADERS, "Content-Type": "text/plain"},
        )
    assert r.status_code == 400
    assert "sessionid" in r.json()["detail"]
