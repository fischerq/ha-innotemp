"""Run scripts/smoke_test.py against the fake controller.

Checks the script works end to end, that ``--record`` produces a fixture set
the fake server can replay, and that recordings contain no credentials.
"""

import argparse
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from tests.fake_innotemp import SYNTHETIC_DIR, FakeInnotemp

pytestmark = pytest.mark.usefixtures("socket_enabled")

SCRIPT = Path(__file__).parent.parent / "scripts" / "smoke_test.py"
PASSWORD = "s3cret-pass"


def _load_script():
    spec = importlib.util.spec_from_file_location("smoke_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(host, **overrides):
    values = dict(
        host=host,
        user="installer",
        password=PASSWORD,
        messages=2,
        sse_timeout=5,
        record=None,
        write_test=None,
        write_value=None,
        no_restore=False,
        yes=True,
        show=3,
        verbose=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
async def fake_host():
    fake = FakeInnotemp(SYNTHETIC_DIR, username="installer", password=PASSWORD)
    server = TestServer(fake.make_app(), host="127.0.0.1")
    await server.start_server()
    try:
        yield fake, f"{server.host}:{server.port}"
    finally:
        fake.expire_sessions()
        await server.close()


@pytest.mark.asyncio
async def test_smoke_script_read_only(fake_host, capsys):
    fake, host = fake_host
    script = _load_script()

    assert await script.run(_args(host)) == 0

    out = capsys.readouterr().out
    assert "login accepted" in out
    assert "PHPSESSID cookie stored" in out
    assert "integration's discovery" in out
    assert "payload format: CSV" in out
    assert "message 2: 6 values match signal names" in out
    assert fake.writes == []  # read-only unless --write-test


@pytest.mark.asyncio
async def test_smoke_script_records_replayable_sanitised_fixtures(fake_host, tmp_path):
    fake, host = fake_host
    script = _load_script()
    record = tmp_path / "recorded"

    assert await script.run(_args(host, record=str(record))) == 0

    names = {p.name for p in record.iterdir()}
    assert names >= {
        "groups.read.json",
        "roomconf.read.json",
        "live_signal.read.json",
        "live_signal.read.SSE.txt",
        "source.json",
    }
    for path in record.iterdir():
        text = path.read_text(encoding="utf-8")
        assert PASSWORD not in text
        assert "installer" not in text
        assert "PHPSESSID" not in text
    roomconf = json.loads((record / "roomconf.read.json").read_text("utf-8"))
    assert roomconf["city"] == "REDACTED"
    assert json.loads((record / "source.json").read_text("utf-8"))["source"] == (
        "recorded"
    )

    # The recording is a valid fixture set for the fake server.
    replay = FakeInnotemp(record)
    assert replay.signal_names == fake.signal_names
    assert len(replay.sse_frames) == 2


@pytest.mark.asyncio
async def test_smoke_script_write_test_noop_and_restore(fake_host, tmp_path):
    fake, host = fake_host
    script = _load_script()

    # Default: write the current value back unchanged.
    assert (
        await script.run(
            _args(host, write_test="003_e17par02_gui001out1", record=str(tmp_path))
        )
        == 0
    )
    assert fake.writes[-1]["val_new"] == fake.writes[-1]["val_prev"] == "2"
    assert (tmp_path / "value.save.json").exists()

    # Explicit value: written, then restored.
    assert (
        await script.run(
            _args(host, write_test="001_mixer001_entry1", write_value="40")
        )
        == 0
    )
    assert [(w["val_new"], w["val_prev"]) for w in fake.writes[-2:]] == [
        ("40", "45"),
        ("45", "40"),
    ]
    assert fake.values["001_mixer001_entry1"] == "45"


@pytest.mark.asyncio
async def test_smoke_script_reports_failures(fake_host, capsys, monkeypatch):
    fake, host = fake_host
    script = _load_script()

    assert await script.run(_args(host, password="wrong")) == 1
    assert "login rejected" in capsys.readouterr().out

    # Unknown parameter: refused before anything is sent.
    assert await script.run(_args(host, write_test="nope")) == 1
    assert fake.writes == []

    # Without Home Assistant the summary falls back to the approximation.
    monkeypatch.setattr(script, "_real_processors", lambda: None)
    assert await script.run(_args(host)) == 0
    assert "approximation" in capsys.readouterr().out
