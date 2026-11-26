"""The permanent attachment: session material, installation and renewal."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.keeper import SessionKeeper
from app.main import app
from app.security import (
    SessionError,
    parse_session_material,
    parse_session_payload,
    read_session,
)
from app.sessions import install_session_from_env, save_session_material

HEADERS = {"X-API-Key": settings.api_key}

SESSIONID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

COOKIE_HEADER = (
    "sessionid=a1b2c3d4e5f60718293a4b5c6d7e8f90; "
    "sessionid_ss=a1b2c3d4e5f60718293a4b5c6d7e8f90; "
    "tt-target-idc=alisg; "
    "ttwid=1%7Cabc%7C1795636673%7Cdead; "
    "msToken=x-ExampleMsTokenValueForTestsOnly000000000000000000000"
)


def install(cookies: dict) -> dict:
    return asyncio.run(save_session_material("", cookies=cookies))


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def test_a_bare_sessionid_becomes_the_binding_cookies():
    cookies = parse_session_material(SESSIONID)
    assert cookies["sessionid"] == SESSIONID
    assert cookies["sessionid_ss"] == SESSIONID
    # without the edge cookie TikTok treats the sessionid as a guest
    assert cookies["tt-target-idc"] == "alisg"


def test_a_cookie_header_is_parsed_unchanged():
    cookies = parse_session_material(COOKIE_HEADER)
    assert cookies["sessionid"] == SESSIONID
    assert cookies["msToken"].startswith("x-Example")
    assert "tt-target-idc" in cookies


def test_json_exports_still_work():
    cookies = parse_session_material('[{"name": "sessionid", "value": "abc123"}]')
    assert cookies["sessionid"] == "abc123"


# --------------------------------------------------------------------------
# installing from the environment
# --------------------------------------------------------------------------
def test_the_environment_installs_the_session(monkeypatch):
    monkeypatch.setattr(settings, "tiktok_session", SESSIONID)
    info = asyncio.run(install_session_from_env())
    assert info["installed"] is True
    stored = parse_session_payload(read_session())
    assert stored["sessionid"] == SESSIONID
    assert stored["tt-target-idc"] == "alisg"


def test_a_stored_session_is_kept_when_the_environment_differs(monkeypatch):
    install({"sessionid": SESSIONID, "tt-target-idc": "alisg"})
    monkeypatch.setattr(settings, "tiktok_session", "b9b9b9b9b9b9b9b9b9b9b9b9b9b9b9b9")
    info = asyncio.run(install_session_from_env())
    assert info["installed"] is False
    stored = parse_session_payload(read_session())
    # the stored (renewed) session wins — the keeper already proved it works
    assert stored["sessionid"] == SESSIONID


def test_forcing_overwrites_the_stored_session(monkeypatch):
    install({"sessionid": SESSIONID, "tt-target-idc": "alisg"})
    monkeypatch.setattr(settings, "tiktok_session", "b9b9b9b9b9b9b9b9b9b9b9b9b9b9b9b9")
    asyncio.run(install_session_from_env(force=True))
    stored = parse_session_payload(read_session())
    assert stored["sessionid"] == "b9b9b9b9b9b9b9b9b9b9b9b9b9b9b9b9"


def test_no_environment_variable_is_not_an_error(monkeypatch):
    monkeypatch.setattr(settings, "tiktok_session", "")
    info = asyncio.run(install_session_from_env())
    assert info["installed"] is False


# --------------------------------------------------------------------------
# the keeper
# --------------------------------------------------------------------------
class FakeBrowser:
    name = "browser"

    def __init__(self, jar: dict, user: dict | None = None):
        self.jar = jar
        self.user = user or {}

    async def harvest_cookies(self) -> dict:
        return dict(self.jar)

    async def account_context(self) -> dict:
        return self.user


class FakeEngine:
    def __init__(self, browser=None, http=None):
        self._browser = browser
        self._http = http
        self.reloads = 0

    async def reload_session(self) -> None:
        self.reloads += 1


def test_renewal_adds_cookies_and_never_drops_one():
    install({"sessionid": SESSIONID, "tt-target-idc": "alisg", "ttwid": "old"})
    browser = FakeBrowser({"msToken": "fresh-token", "ttwid": "new"})
    engine = FakeEngine(browser=browser)
    keeper = SessionKeeper(engine)

    result = asyncio.run(keeper.renew())

    assert result["renewed"] is True
    assert set(result["changed"]) == {"msToken", "ttwid"}
    stored = parse_session_payload(read_session())
    assert stored["sessionid"] == SESSIONID          # binding kept
    assert stored["tt-target-idc"] == "alisg"        # edge kept
    assert stored["msToken"] == "fresh-token"        # refreshed
    assert stored["ttwid"] == "new"
    assert engine.reloads == 1


def test_a_rotated_sessionid_is_kept_and_reported():
    install({"sessionid": SESSIONID, "tt-target-idc": "alisg"})
    browser = FakeBrowser({"sessionid": "rotated-sessionid-value"})
    keeper = SessionKeeper(FakeEngine(browser=browser))

    result = asyncio.run(keeper.renew())

    assert result["sessionid_rotated"] is True
    stored = parse_session_payload(read_session())
    assert stored["sessionid"] == "rotated-sessionid-value"


def test_healing_falls_back_to_the_environment_when_the_account_is_gone(monkeypatch):
    install({"sessionid": SESSIONID, "tt-target-idc": "alisg"})
    monkeypatch.setattr(settings, "tiktok_session", "cccccccccccccccccccccccccccccccc")

    # the browser answers, but nobody is signed in
    browser = FakeBrowser({"msToken": "fresh"}, user={})
    keeper = SessionKeeper(FakeEngine(browser=browser))

    asyncio.run(keeper.heal())

    stored = parse_session_payload(read_session())
    # the permanent copy took over
    assert stored["sessionid"] == "cccccccccccccccccccccccccccccccc"
    assert keeper.reseeds == 1


def test_status_reports_the_attachment():
    install({"sessionid": SESSIONID, "tt-target-idc": "alisg"})
    browser = FakeBrowser({}, user={"id": "7712199357596075015"})
    keeper = SessionKeeper(FakeEngine(browser=browser))

    assert asyncio.run(keeper.check_attached()) is True
    status = keeper.status()
    assert status["attached"] is True
    assert status["present"] is True
    assert status["sessionid"] == "a1b2c3…8f90"


def test_a_missing_session_is_reported_not_raised():
    from app.security import delete_session

    delete_session()
    keeper = SessionKeeper(FakeEngine())
    assert keeper.status()["present"] is False


# --------------------------------------------------------------------------
# the API
# --------------------------------------------------------------------------
def test_health_reports_the_keeper():
    with TestClient(app) as c:
        body = c.get("/health").json()
    assert "session" in body


def test_the_renew_endpoint_answers(monkeypatch):
    from app.adapters.http import HttpBackend

    async def no_network(self):
        return {}

    monkeypatch.setattr(HttpBackend, "renew_session", no_network)
    with TestClient(app) as c:
        assert c.post("/admin/session/renew").status_code == 401
        r = c.post("/admin/session/renew", headers=HEADERS)
    # no keeper runs in the test process (BACKEND=http, no browser)
    assert r.status_code in (200, 502, 503)


def test_the_session_info_endpoint_answers():
    with TestClient(app) as c:
        r = c.get("/admin/session", headers=HEADERS)
    assert r.status_code == 200
    assert "keeper" in r.json()
