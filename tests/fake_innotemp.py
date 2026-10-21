"""A fake Innotemp controller (aiohttp.web) for integration-style tests.

It implements the endpoints from docs/API_SPEC.md closely enough to run the
real ``InnotempApiClient`` (and the whole integration) against it:

* ``POST /inc/groups.read.php`` -- login; sets a ``PHPSESSID`` cookie
* ``POST /inc/roomconf.read.php`` -- configuration (with current values)
* ``POST /inc/live_signal.read.php`` -- signal names (``init=1``)
* ``GET  /inc/live_signal.read.SSE.php`` -- SSE stream of values
* ``POST /inc/value.save.php`` -- write with ``val_prev`` optimistic locking

Every endpoint except login requires a valid session; without one the server
answers with a 302 redirect to the login page, which is how the client
detects an expired session. ``expire_sessions()`` simulates a timeout.

Responses come from a fixture directory (see ``tests/fixtures/``):
``groups.read.json``, ``roomconf.read.json``, ``live_signal.read.json``,
``live_signal.read.SSE.txt`` (raw stream body, replayed in a loop) and the
optional ``value.save.json``. ``tests/fixtures/synthetic`` is hand-written;
``scripts/smoke_test.py --record`` produces a set recorded from a real device
in the same layout.
"""

from __future__ import annotations

import asyncio
import copy
import json
import secrets
from pathlib import Path
from typing import Any

from aiohttp import web

from custom_components.innotemp.api import format_api_value, parse_sse_data

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYNTHETIC_DIR = FIXTURES_DIR / "synthetic"
RECORDED_DIR = FIXTURES_DIR / "recorded"

ACCESS_DENIED = {"info": "error", "error": "Access denied."}


def _walk_params(node: Any):
    """Yield every dict in a roomconf tree that looks like a parameter."""
    if isinstance(node, dict):
        if node.get("var") and node.get("unit"):
            yield node
        for key, value in node.items():
            if not key.startswith("@"):
                yield from _walk_params(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_params(item)


def _parse_sse_frames(text: str) -> tuple[list[str], list[str]]:
    """Split a raw SSE body into (preamble lines, data payloads)."""
    preamble: list[str] = []
    frames: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            frames.append(line[5:].strip())
        elif line.strip() and not frames:
            preamble.append(line)
    return preamble, frames


class FakeInnotemp:
    """State and request handlers of one fake controller."""

    def __init__(
        self,
        fixture_dir: Path = SYNTHETIC_DIR,
        username: str = "user",
        password: str = "secret",
        sse_interval: float = 0.02,
    ) -> None:
        self.fixture_dir = Path(fixture_dir)
        self.username = username
        self.password = password
        self.sse_interval = sse_interval

        self.login_response = self._load_json("groups.read.json")
        self.roomconf = self._load_json("roomconf.read.json")
        self.signal_names: list[str] = self._load_json("live_signal.read.json")
        self.sse_preamble, self.sse_frames = _parse_sse_frames(
            (self.fixture_dir / "live_signal.read.SSE.txt").read_text("utf-8")
        )
        save_path = self.fixture_dir / "value.save.json"
        self.save_response = (
            json.loads(save_path.read_text("utf-8"))
            if save_path.exists()
            else {"info": "success"}
        )

        # Current value of every parameter: roomconf values, then the first
        # SSE frame for live signals.
        self.values: dict[str, str] = {}
        for param in _walk_params(self.roomconf):
            for key in ("#text", "value", "val"):
                if key in param:
                    self.values[param["var"]] = str(param[key])
                    break
        self.room_of_param: dict[str, int] = {}
        self._index_rooms()
        if self.sse_frames:
            first = parse_sse_data(self.sse_frames[0]) or []
            for name, value in zip(self.signal_names, first):
                self.values.setdefault(name, str(value))
        # Parameters written through value.save.php override replayed frames.
        self.overrides: dict[str, str] = {}

        self.sessions: set[str] = set()
        self.logins = 0
        self.failed_logins = 0
        self.redirects = 0
        self.sse_connections = 0
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.writes: list[dict[str, str]] = []
        # Set to (content_type, body) to make value.save.php misbehave.
        self.save_override: tuple[str, str] | None = None

    # -- helpers -------------------------------------------------------------

    def _load_json(self, name: str) -> Any:
        return json.loads((self.fixture_dir / name).read_text("utf-8"))

    def _index_rooms(self) -> None:
        import re

        for rooms in self.roomconf.values():
            if not isinstance(rooms, list):
                continue
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                match = re.search(
                    r"room(\d+)", room.get("@attributes", {}).get("type", "")
                )
                if not match:
                    continue
                for param in _walk_params(room):
                    self.room_of_param[param["var"]] = int(match.group(1))

    def expire_sessions(self) -> None:
        """Forget every session, like a PHP session timeout."""
        self.sessions.clear()

    def _has_session(self, request: web.Request) -> bool:
        return request.cookies.get("PHPSESSID") in self.sessions

    def _redirect_to_login(self) -> web.Response:
        self.redirects += 1
        return web.Response(status=302, headers={"Location": "/index.php"})

    def _frame(self, index: int) -> str:
        raw = self.sse_frames[index % len(self.sse_frames)]
        values = parse_sse_data(raw) or []
        for i, name in enumerate(self.signal_names[: len(values)]):
            if name in self.overrides:
                values[i] = self.overrides[name]
        if raw.lstrip().startswith("["):
            return json.dumps(values)
        return ",".join(str(v) for v in values)

    # -- handlers ------------------------------------------------------------

    async def login(self, request: web.Request) -> web.Response:
        form = await request.post()
        self.requests.append(("groups.read.php", dict(form)))
        if form.get("un") != self.username or form.get("pw") != self.password:
            self.failed_logins += 1
            return web.json_response(ACCESS_DENIED)
        self.logins += 1
        sid = secrets.token_hex(13)
        self.sessions.add(sid)
        response = web.json_response(self.login_response)
        response.set_cookie("PHPSESSID", sid, path="/")
        return response

    async def roomconf_read(self, request: web.Request) -> web.Response:
        form = await request.post()
        self.requests.append(("roomconf.read.php", dict(form)))
        if not self._has_session(request):
            return self._redirect_to_login()
        config = copy.deepcopy(self.roomconf)
        for param in _walk_params(config):
            if param["var"] in self.overrides and "value" in param:
                param["value"] = self.overrides[param["var"]]
        return web.json_response(config)

    async def live_signal_read(self, request: web.Request) -> web.Response:
        form = await request.post()
        self.requests.append(("live_signal.read.php", dict(form)))
        if not self._has_session(request):
            return self._redirect_to_login()
        return web.json_response(self.signal_names)

    async def live_signal_sse(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(("live_signal.read.SSE.php", {}))
        if not self._has_session(request):
            return self._redirect_to_login()
        sid = request.cookies["PHPSESSID"]
        self.sse_connections += 1
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        )
        await response.prepare(request)
        for line in self.sse_preamble:
            await response.write(f"{line}\n".encode())
        await response.write(b"\n")
        index = 0
        # Stream until the session expires (the real device presumably keeps
        # going; ending the stream lets tests exercise the reconnect path).
        try:
            while sid in self.sessions and self.sse_frames:
                await response.write(f"data: {self._frame(index)}\n\n".encode())
                index += 1
                await asyncio.sleep(self.sse_interval)
        except ConnectionResetError:
            pass  # client went away
        return response

    async def value_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        self.requests.append(("value.save.php", form))
        if not self._has_session(request):
            return self._redirect_to_login()
        if self.save_override is not None:
            content_type, body = self.save_override
            return web.Response(text=body, content_type=content_type)
        self.writes.append(form)
        param = form.get("param", "")
        if param not in self.room_of_param:
            return web.json_response({"info": "error", "error": "Unknown parameter"})
        if str(self.room_of_param[param]) != form.get("room_id"):
            return web.json_response({"info": "error", "error": "Wrong room"})
        current = format_api_value(self.values.get(param))
        if format_api_value(form.get("val_prev")) != current:
            return web.json_response(
                {"info": "error", "error": "Value was changed", "val": current}
            )
        new = format_api_value(form.get("val_new"))
        self.values[param] = new
        self.overrides[param] = new
        return web.json_response(self.save_response)

    def make_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/inc/groups.read.php", self.login)
        app.router.add_post("/inc/roomconf.read.php", self.roomconf_read)
        app.router.add_post("/inc/live_signal.read.php", self.live_signal_read)
        app.router.add_get("/inc/live_signal.read.SSE.php", self.live_signal_sse)
        app.router.add_post("/inc/value.save.php", self.value_save)
        return app
