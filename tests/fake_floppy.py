"""In-process fake Floppy server for tests.

    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda req: (200, {"ok": 1}))
        http.request("POST", fake.url + "/api/v1/scrobble/", json_body={...})
        fake.requests  # list of Request, in arrival order

Handler: ``handler(request) -> (status, body_dict_or_None)``. ``request`` has
``.method``, ``.path`` (no query), ``.query`` (dict), ``.headers``, ``.json``
(parsed JSON body or None), ``.form`` (parsed urlencoded body or None).
Routes match on exact (method, path). Unrouted requests get 599 and make
``__exit__`` raise AssertionError listing them.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: Any
    json: Any = None
    form: dict[str, str] | None = None


Handler = Callable[[Request], "tuple[int, dict | None]"]


class FakeFloppy:
    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.failures: list[str] = []
        self._routes: dict[tuple[str, str], Handler] = {}
        fake = self

        class _H(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                parts = urlsplit(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                ctype = self.headers.get("Content-Type", "")
                req = Request(self.command, parts.path, dict(parse_qsl(parts.query)), self.headers)
                if raw and "json" in ctype:
                    req.json = json.loads(raw)
                elif raw and "x-www-form-urlencoded" in ctype:
                    req.form = dict(parse_qsl(raw.decode()))
                fake.requests.append(req)
                handler = fake._routes.get((req.method, req.path))
                if handler is None:
                    fake.failures.append(f"{req.method} {req.path}")
                    status, body = 599, {"error": "unrouted"}
                else:
                    status, body = handler(req)
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _handle  # noqa: N815

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def route(self, method: str, path: str, handler: Handler) -> None:
        self._routes[(method, path)] = handler

    def __enter__(self) -> FakeFloppy:
        threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        if exc_type is None and self.failures:
            raise AssertionError("unrouted requests: " + ", ".join(self.failures))
