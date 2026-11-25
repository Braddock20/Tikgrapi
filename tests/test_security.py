"""Cookie parsing, sessionid extraction and session normalisation."""
from __future__ import annotations

import pytest

from app.security import (
    ESSENTIAL_COOKIES,
    SessionError,
    extract_sessionid,
    filter_for_http,
    hash_sessionid,
    mask_sessionid,
    normalize_cookies,
    parse_cookie_header,
    parse_cookie_json,
    parse_session_payload,
    to_session_payload,
)

COOKIE_HEADER = (
    "ttwid=1%7Cabc%7C1795636673%7Cdead; "
    "msToken=x-ExampleMsTokenValueForTestsOnly000000000000000000000; "
    "s_v_web_id=verify_mwwjerri_0iYlonMU_Tril_4DCp_8vq9_Cm0kuhdiIoRO; "
    "odin_tt=7b052d15c99eadf6813bcfe039c611259; "
    "sessionid=a1b2c3d4e5f60718293a4b5c6d7e8f90; "
    "sessionid_ss=a1b2c3d4e5f60718293a4b5c6d7e8f90; "
    "tt_csrf_token=testCsrfTokenValue000000000000000; "
    "tt-target-idc=alisg; "
    "tt-target-idc-sign=YoSaqhfX9cROEJsM1Fn_2Nu4FwCxTZCI02DY; "
    "store-idc=alisg; store-country-code=ke; store-country-code-src=uid; "
    "store-country-sign=MEIEDB5tTD; "
    "perf_feed_cache={%22expireTimestamp%22:1796238000000%2C%22itemIds%22:[]}; "
    "last_login_method=google"
)


def test_parse_cookie_header_extracts_every_pair():
    cookies = parse_cookie_header(COOKIE_HEADER)
    assert cookies["sessionid"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert cookies["store-country-code"] == "ke"
    assert len(cookies) == 15


def test_cookie_values_are_url_decoded():
    cookies = parse_cookie_header("s_v_web_id=verify_mwwjerri_0iYlonMU_Tril_4DCp")
    assert cookies["s_v_web_id"] == "verify_mwwjerri_0iYlonMU_Tril_4DCp"


def test_extract_sessionid_prefers_the_primary_cookie():
    cookies = parse_cookie_header(COOKIE_HEADER)
    assert extract_sessionid(cookies) == "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def test_extract_sessionid_falls_back_to_the_ss_cookie():
    assert extract_sessionid({"sessionid_ss": "abc"}) == "abc"


def test_extract_sessionid_raises_when_absent():
    with pytest.raises(SessionError):
        extract_sessionid({"msToken": "x"})


def test_mask_and_hash_do_not_leak_the_secret():
    sessionid = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    masked = mask_sessionid(sessionid)
    assert masked.startswith("a1b2c3") and masked.endswith("8f90")
    assert sessionid not in masked
    assert hash_sessionid(sessionid) == hash_sessionid(sessionid)
    assert sessionid not in hash_sessionid(sessionid)


def test_parse_cookie_json_accepts_the_cookie_editor_export():
    payload = '[{"name": "sessionid", "value": "abc", "domain": ".tiktok.com", "path": "/"}]'
    assert parse_cookie_json(payload) == {"sessionid": "abc"}


def test_parse_cookie_json_accepts_a_wrapper_object():
    payload = '{"cookies": [{"name": "sessionid", "value": "abc"}]}'
    assert parse_cookie_json(payload) == {"sessionid": "abc"}


def test_parse_session_payload_detects_the_format():
    assert parse_session_payload(COOKIE_HEADER)["sessionid"].startswith("a1b2c3")
    assert parse_session_payload('{"sessionid": "abc"}') == {"sessionid": "abc"}


def test_parse_session_payload_rejects_garbage():
    with pytest.raises(SessionError):
        parse_session_payload("just some words")


def test_normalize_keeps_every_cookie_by_default():
    cookies = parse_cookie_header(COOKIE_HEADER)
    kept, dropped = normalize_cookies(cookies)
    assert dropped == []
    assert len(kept) == len(cookies)
    # the region cookies carry the account's country: dropping them loses login
    assert "store-country-code" in kept


def test_filter_for_http_removes_only_the_idc_pair():
    cookies = parse_cookie_header(COOKIE_HEADER)
    kept, dropped = filter_for_http(cookies)
    assert set(dropped) == {"tt-target-idc", "tt-target-idc-sign"}
    assert "store-country-code" in kept
    assert "sessionid" in kept


def test_essential_cookies_are_present_in_a_real_session():
    cookies = parse_cookie_header(COOKIE_HEADER)
    for name in ESSENTIAL_COOKIES:
        assert name in cookies


def test_session_payload_shape_matches_the_library_format():
    payload = to_session_payload({"sessionid": "abc"})
    assert payload == [
        {"name": "sessionid", "value": "abc", "domain": ".tiktok.com", "path": "/"}
    ]
