#!/usr/bin/env python3
"""Live smoke test of the Innotemp integration against a real controller.

Runs the integration's own API client (custom_components/innotemp/api.py)
without Home Assistant:

  1. logs in (and checks the PHPSESSID cookie was kept),
  2. fetches roomconf and prints the entities the integration would create,
  3. fetches the SSE signal names and reads N SSE messages, checking each
     message has one value per signal name,
  4. optionally records sanitised responses as a fixture set for
     tests/fake_innotemp.py (--record),
  5. optionally performs ONE write (--write-test PARAM). Writes are opt-in
     and ask for confirmation unless --yes is given. Without --write-value
     the current value is written back unchanged.

Only aiohttp is required. If Home Assistant is installed too, the entity
summary uses the integration's real platform discovery; otherwise it uses a
close approximation and says so.

Example:
  python scripts/smoke_test.py --host 192.168.25.204 --user X --password Y \\
      --record tests/fixtures/recorded/

Exit status: 0 if every check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import getpass
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

import aiohttp

REPO = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO / "custom_components" / "innotemp"

REDIRECT_STATUSES = (301, 302, 303, 307, 308)
# Keys whose values must never end up in a recorded fixture.
SENSITIVE_KEYS = {
    "pw",
    "pass",
    "passwd",
    "password",
    "un",
    "user",
    "username",
    "login",
    "phpsessid",
    "sid",
    "session",
    "session_id",
    "cookie",
    "token",
    "email",
    "mail",
    "city",
    "lat",
    "lon",
    "latitude",
    "longitude",
}
REDACTED = "REDACTED"


def _load_module(name: str, filename: str):
    """Import a module from the integration by path, bypassing __init__.py.

    custom_components/innotemp/__init__.py imports Home Assistant; api.py and
    api_parser.py only need aiohttp and the standard library.
    """
    spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = _load_module("innotemp_api", "api.py")
api_parser = _load_module("innotemp_api_parser", "api_parser.py")


# --- output helpers -----------------------------------------------------------


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"  [ OK ] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"  [WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"  [FAIL] {message}")


def section(title: str) -> None:
    print(f"\n== {title}")


# --- sanitising -------------------------------------------------------------------


def sanitize(value: Any, secrets: list[str]) -> Any:
    """Return a copy of a JSON value with credentials and location removed."""
    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if str(key).lower() in SENSITIVE_KEYS
                else sanitize(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, secrets)
    return value


def sanitize_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 3:
            text = text.replace(secret, REDACTED)
    return text


# --- entity discovery -------------------------------------------------------------


def _approx_processors() -> dict[str, tuple[list[str], Any]]:
    """Approximation of the platforms' discovery, used without Home Assistant."""
    all_keys = [
        "display",
        "param",
        "mixer",
        "pump",
        "piseq",
        "radiator",
        "drink",
        "main",
    ]

    def make(predicate, needs_room):
        def processor(item, room, room_id, comp, hint):
            if not item.get("var") or not item.get("unit"):
                return None
            if needs_room and room_id is None:
                return None
            if not predicate(item["unit"]):
                return None
            return {
                "param_id": item["var"],
                "label": item.get("label"),
                "unit": item["unit"],
                "room_id": room_id,
                "source": hint,
            }

        return processor

    return {
        "sensor": (all_keys, make(lambda unit: True, False)),
        "number": (
            ["param", "mixer", "piseq", "radiator", "drink", "main"],
            make(lambda unit: unit not in ("ONOFF", "ONOFFAUTO"), False),
        ),
        "select": (all_keys, make(lambda unit: unit == "ONOFFAUTO", True)),
        "switch": (all_keys, make(lambda unit: unit == "ONOFF", True)),
    }


def _real_processors() -> dict[str, tuple[list[str], Any]] | None:
    """The integration's own discovery, if Home Assistant is importable."""
    try:
        sys.path.insert(0, str(REPO))
        from custom_components.innotemp import number, select, sensor, switch
    except Exception:  # noqa: BLE001 - HA missing or broken: fall back
        return None
    finally:
        sys.path.remove(str(REPO))

    def wrap(fn):
        def processor(item, room, room_id, comp, hint):
            data = fn(item, room, room_id, comp, hint)
            if not data:
                return None
            source = data.get("param_data") or data.get("item_data") or item
            return {
                "param_id": data["param_id"],
                "label": source.get("label"),
                "unit": source.get("unit"),
                "room_id": data.get("numeric_room_id", room_id),
                "source": hint,
            }

        return processor

    return {
        "sensor": (sensor.CONTAINER_KEYS, wrap(sensor._create_sensor_entity_data)),
        "number": (number.CONTAINER_KEYS, wrap(number._create_number_entity_data)),
        "select": (select.CONTAINER_KEYS, wrap(select._create_select_entity_data)),
        "switch": (switch.CONTAINER_KEYS, wrap(switch._create_switch_entity_data)),
    }


def discover(config: dict) -> tuple[dict[str, list[dict]], bool]:
    processors = _real_processors()
    exact = processors is not None
    if processors is None:
        processors = _approx_processors()
    result = {}
    for platform, (keys, processor) in processors.items():
        result[platform] = api_parser.process_room_config_data(config, keys, processor)
    return result, exact


# --- steps --------------------------------------------------------------------------


async def raw_login_body(session, base_url, user, password) -> Any:
    """Fetch the login response body for recording (the client discards it)."""
    async with session.post(
        f"{base_url}/groups.read.php",
        data={"un": user, "pw": password},
        allow_redirects=False,
        timeout=api.REQUEST_TIMEOUT,
    ) as response:
        text = await response.text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_non_json_body": text[:500]}


async def read_sse(session, base_url, count: int, timeout: float):
    """Read ``count`` data messages; return (raw lines, parsed value lists)."""
    raw_lines: list[str] = []
    messages: list[list | None] = []
    async with asyncio.timeout(timeout):
        async with session.get(
            f"{base_url}/live_signal.read.SSE.php",
            allow_redirects=False,
            timeout=api.SSE_TIMEOUT,
        ) as response:
            if response.status in REDIRECT_STATUSES:
                raise api.InnotempAuthError(
                    f"SSE redirected to {response.headers.get('Location')}"
                )
            response.raise_for_status()
            print(f"  content-type: {response.headers.get('Content-Type')}")
            async for line in response.content:
                raw_lines.append(line.decode("utf-8", errors="replace"))
                if line.startswith(b"data:"):
                    messages.append(api.parse_sse_data(line))
                    if len(messages) >= count:
                        break
    return raw_lines, messages


def write_fixture(directory: Path, name: str, payload: Any) -> None:
    path = directory / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"  wrote {path}")


async def run(args: argparse.Namespace) -> int:
    report = Report()
    secrets = [args.user, args.password]
    host = api.normalize_host(args.host)
    record_dir = Path(args.record) if args.record else None
    if record_dir:
        record_dir.mkdir(parents=True, exist_ok=True)

    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        client = api.InnotempApiClient(session, host, args.user, args.password)
        base_url = client.base_url

        section(f"Login to {base_url}")
        if record_dir:
            body = await raw_login_body(session, base_url, args.user, args.password)
            write_fixture(record_dir, "groups.read.json", sanitize(body, secrets))
        try:
            await client.async_login()
        except api.InnotempConnectionError as err:
            report.fail(f"controller not reachable: {err}")
            return 1
        except api.InnotempAuthError as err:
            report.fail(f"login rejected: {err}")
            return 1
        report.ok("login accepted")
        cookies = {cookie.key for cookie in session.cookie_jar}
        if "PHPSESSID" in cookies:
            report.ok("PHPSESSID cookie stored")
        else:
            report.warn(f"no PHPSESSID cookie stored (cookies: {sorted(cookies)})")

        section("Configuration (roomconf.read.php)")
        config = await client.async_get_config()
        if not config:
            report.fail("roomconf returned no data")
            return 1
        report.ok(f"roomconf top-level keys: {sorted(config)}")
        if record_dir:
            write_fixture(record_dir, "roomconf.read.json", sanitize(config, secrets))
        initial_states = api_parser.extract_initial_states(config)
        print(f"  initial values found in roomconf: {len(initial_states)}")
        if not initial_states:
            report.warn(
                "roomconf carries no current values the integration can read "
                "('#text'/'value'/'val'); settings will be unknown until SSE "
                "delivers them (and stay unknown if they are not live signals)"
            )

        entities, exact = discover(config)
        how = "integration's discovery" if exact else "approximation (HA not installed)"
        print(f"  entities ({how}):")
        for platform, items in entities.items():
            print(f"    {platform:7s} {len(items):4d}")
            for item in items[: args.show]:
                print(
                    f"            {item['param_id']}  room={item['room_id']}  "
                    f"unit={item['unit']!r}  [{item['source']}]  {item['label']!r}"
                )
            if len(items) > args.show:
                print(f"            ... {len(items) - args.show} more (--show N)")
        if not any(entities.values()):
            report.fail("no entities discovered from roomconf")
        else:
            report.ok(f"{sum(map(len, entities.values()))} entities discovered")
        writable_from_io = [
            i["param_id"]
            for i in entities["number"]
            if i["source"].endswith((".input", ".output"))
        ]
        if writable_from_io:
            report.warn(
                f"{len(writable_from_io)} number entities come from inputs/outputs "
                f"(measured values?), e.g. {writable_from_io[:3]}"
            )

        section("SSE signal names (live_signal.read.php)")
        signal_names = await client.async_get_signal_names()
        report.ok(f"{len(signal_names)} signal names")
        if record_dir:
            write_fixture(
                record_dir, "live_signal.read.json", sanitize(signal_names, secrets)
            )
        all_params = {i["param_id"] for items in entities.values() for i in items}
        not_in_config = [n for n in signal_names if n not in all_params]
        if not_in_config:
            print(
                f"  {len(not_in_config)} signals have no entity, e.g. "
                f"{not_in_config[:5]}"
            )
        no_value = sorted(all_params - set(signal_names) - set(initial_states))
        if no_value:
            report.warn(
                f"{len(no_value)} entity params are neither live signals nor have "
                f"a roomconf value (they will stay 'unknown'), e.g. {no_value[:5]}"
            )

        section(f"SSE stream ({args.messages} messages)")
        messages: list = []
        try:
            raw_lines, messages = await read_sse(
                session, base_url, args.messages, args.sse_timeout
            )
        except TimeoutError:
            report.fail(f"no {args.messages} SSE messages within {args.sse_timeout}s")
            raw_lines = []
        except (api.InnotempApiError, aiohttp.ClientError) as err:
            report.fail(f"SSE failed: {err}")
            raw_lines = []
        data_lines = [line for line in raw_lines if line.startswith("data:")]
        if data_lines:
            fmt = "JSON array" if data_lines[0][5:].strip().startswith("[") else "CSV"
            print(f"  payload format: {fmt}; first line: {data_lines[0][:160]!r}")
        for index, values in enumerate(messages, 1):
            if values is None:
                report.fail(f"message {index}: could not parse")
            elif len(values) != len(signal_names):
                report.fail(
                    f"message {index}: {len(values)} values for "
                    f"{len(signal_names)} signal names"
                )
            else:
                report.ok(f"message {index}: {len(values)} values match signal names")
        if messages and messages[-1] and len(messages[-1]) == len(signal_names):
            sample = dict(list(zip(signal_names, messages[-1]))[: args.show])
            print(f"  sample: {sample}")
        if record_dir and raw_lines:
            write_fixture(
                record_dir,
                "live_signal.read.SSE.txt",
                sanitize_text("".join(raw_lines), secrets),
            )

        if args.write_test:
            section(f"Write test ({args.write_test})")
            await write_test(
                args,
                client,
                config,
                signal_names,
                messages,
                initial_states,
                report,
                record_dir,
                secrets,
            )

    if record_dir:
        write_fixture(
            record_dir,
            "source.json",
            {
                "source": "recorded",
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "tool": "scripts/smoke_test.py",
                "note": "Recorded from a real controller; credentials, cookies "
                "and location fields replaced with REDACTED. Review before "
                "committing.",
            },
        )

    section("Result")
    if report.failures:
        print(
            f"  {len(report.failures)} check(s) FAILED, {len(report.warnings)} warning(s)"
        )
        return 1
    print(f"  all checks passed, {len(report.warnings)} warning(s)")
    return 0


async def write_test(
    args,
    client,
    config,
    signal_names,
    messages,
    initial_states,
    report,
    record_dir,
    secrets,
) -> None:
    param = args.write_test
    room_ids = {}

    def collect(item, room, room_id, comp, hint):
        if item.get("var"):
            room_ids[item["var"]] = room_id
        return None

    keys = ["display", "param", "mixer", "pump", "piseq", "radiator", "drink", "main"]
    api_parser.process_room_config_data(config, keys, collect)
    if param not in room_ids or room_ids[param] is None:
        report.fail(f"{param} not found in roomconf (or it has no numeric room id)")
        return
    room_id = room_ids[param]

    current = None
    if param in signal_names and messages and messages[-1]:
        current = messages[-1][signal_names.index(param)]
    if current is None:
        current = initial_states.get(param)
    if current is None:
        report.fail(f"current value of {param} unknown; refusing to write")
        return

    new = args.write_value if args.write_value is not None else current
    print(
        f"  room_id={room_id} param={param} current={current!r} -> new={new!r}"
        + (
            "  (no-op: writes the current value back)"
            if args.write_value is None
            else ""
        )
    )
    if not args.yes:
        answer = input("  Write this value to the controller? Type 'yes': ")
        if answer.strip().lower() != "yes":
            report.warn("write test skipped by user")
            return

    try:
        response = await client.async_send_command(
            room_id, param, new, val_prev=current
        )
    except api.InnotempApiError as err:
        report.fail(f"write rejected: {err}")
        return
    report.ok(f"write accepted: {response}")
    if record_dir:
        write_fixture(record_dir, "value.save.json", sanitize(response, secrets))

    if args.write_value is not None and not args.no_restore:
        try:
            await client.async_send_command(room_id, param, current, val_prev=new)
            report.ok(f"restored {param} to {current!r}")
        except api.InnotempApiError as err:
            report.fail(f"could not restore {param} to {current!r}: {err}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", required=True, help="controller IP or hostname")
    parser.add_argument("--user", required=True, help="web interface username")
    parser.add_argument(
        "--password", help="web interface password (prompted if omitted)"
    )
    parser.add_argument(
        "--messages", type=int, default=3, help="SSE messages to read (default 3)"
    )
    parser.add_argument(
        "--sse-timeout", type=float, default=60, help="seconds to wait for SSE"
    )
    parser.add_argument(
        "--record",
        metavar="DIR",
        help="write sanitised responses as a fixture set, e.g. tests/fixtures/recorded/",
    )
    parser.add_argument(
        "--write-test",
        metavar="PARAM",
        help="opt-in: write to this parameter id (asks for confirmation)",
    )
    parser.add_argument(
        "--write-value",
        help="value to write (default: write the current value back unchanged)",
    )
    parser.add_argument(
        "--no-restore",
        action="store_true",
        help="keep --write-value instead of restoring the previous value",
    )
    parser.add_argument("--yes", action="store_true", help="do not ask before writing")
    parser.add_argument(
        "--show", type=int, default=5, help="entities/values to list per platform"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()
    if args.password is None:
        args.password = getpass.getpass("Password: ")
    try:
        api.normalize_host(args.host)
    except ValueError as err:
        parser.error(str(err))

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
