from __future__ import annotations

import json

import pytest
import xbmc


def _call(id_: int, method: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "method": method})


def test_unregistered_rpc_method_raises() -> None:
    with pytest.raises(AssertionError, match="VideoLibrary.GetMovies"):
        xbmc.executeJSONRPC(_call(1, "VideoLibrary.GetMovies"))


def test_registered_rpc_method_dispatches() -> None:
    xbmc.RPC_HANDLERS["JSONRPC.Ping"] = lambda params: {"pong": True}
    out = json.loads(xbmc.executeJSONRPC(_call(7, "JSONRPC.Ping")))
    assert out == {"jsonrpc": "2.0", "id": 7, "result": {"pong": True}}


def test_state_is_reset_between_tests() -> None:
    assert xbmc.RPC_HANDLERS == {}
    assert xbmc.LOG == []
