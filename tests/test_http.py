from __future__ import annotations

import socket

import pytest
from fake_floppy import FakeFloppy
from floppy.http import FloppyError, request


def test_request_sends_bearer_and_parses_json():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer t"
        return 200, {"ok": 1}

    with FakeFloppy() as fake:
        fake.route("GET", "/x", handler)
        assert request("GET", fake.url + "/x", token="t") == {"ok": 1}


def test_request_sends_api_key_json_and_form():
    with FakeFloppy() as fake:
        fake.route("POST", "/j", lambda r: (200, {"k": r.headers["X-API-Key"], "j": r.json}))
        fake.route("POST", "/f", lambda r: (200, {"f": r.form}))
        out = request("POST", fake.url + "/j", api_key="k", json_body={"a": 1})
        assert out == {"k": "k", "j": {"a": 1}}
        out = request("POST", fake.url + "/f", form={"grant_type": "x y"})
        assert out == {"f": {"grant_type": "x y"}}


def test_request_empty_body_is_empty_dict():
    with FakeFloppy() as fake:
        fake.route("PUT", "/e", lambda r: (204, None))
        assert request("PUT", fake.url + "/e", json_body={}) == {}


def test_request_raises_with_code_on_409():
    with FakeFloppy() as fake:
        fake.route("GET", "/c", lambda r: (409, {"code": "cursor_expired"}))
        with pytest.raises(FloppyError) as e:
            request("GET", fake.url + "/c")
    assert e.value.status == 409
    assert e.value.code == "cursor_expired"
    assert e.value.body == {"code": "cursor_expired"}
    assert "409" in str(e.value) and "cursor_expired" in str(e.value)


def test_request_oauth_error_and_non_json_body():
    with FakeFloppy() as fake:
        fake.route("POST", "/o", lambda r: (400, {"error": "authorization_pending"}))
        with pytest.raises(FloppyError) as e:
            request("POST", fake.url + "/o", form={})
    assert e.value.code == "authorization_pending"

    from floppy import http

    err = http._error(502, b"<html>bad gateway</html>")
    assert err.status == 502 and err.code is None and err.body == {}


def test_request_network_error_has_no_status():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(FloppyError) as e:
        request("GET", f"http://127.0.0.1:{port}/", timeout=2)
    assert e.value.status is None
