# Innotemp Heating Controller Home Assistant Integration

This is a custom component for Home Assistant to integrate with Innotemp heating controllers. It allows you to read sensor data and control certain parameters exposed by the controller's web API.

**Please Note:** This is a work in progress and may not support all features of the Innotemp controller.

## Supported Features

*   Reading sensor values (e.g., temperatures, battery SOC), pushed live via the controller's event stream
*   Controlling ON/OFF/AUTO parameters (select), ON/OFF parameters (switch) and numeric settings (number)

Changes are sent with the parameter's current value as `val_prev`, which the controller uses to reject a write if the value changed in the meantime. A rejected or unconfirmed write shows an error in Home Assistant instead of silently snapping back.

## Installation

### Via HACS (Recommended)

1.  Ensure you have HACS (Home Assistant Community Store) installed.
2.  In Home Assistant, navigate to HACS -> Integrations.
3.  Click on the three dots in the top right corner and select "Custom repositories".
4.  Add the URL of this repository (`https://github.com/fischerq/ha-innotemp`) as a `Integration` type.
5.  Close the custom repositories dialog.
6.  Search for "Innotemp" in the HACS Integrations section and click on it.
7.  Click "Download" and select the latest version.
8.  Restart Home Assistant.

### Manual Installation

1.  Navigate to your Home Assistant configuration directory (where your `configuration.yaml` is located).
2.  Create a `custom_components` folder if it doesn't exist.
3.  Inside `custom_components`, create a folder named `innotemp`.
4.  Copy all files from the `custom_components/innotemp` directory of this repository into the `custom_components/innotemp` folder you created.
5.  Restart Home Assistant.

## Configuration

1.  After restarting Home Assistant, go to Settings -> Devices & Services.
2.  Click on "Add Integration".
3.  Search for "Innotemp Heating Controller".
4.  Enter the required information:
    *   **Host:** The IP address or hostname of your Innotemp controller (e.g., `192.168.25.204`).
    *   **Username:** Your username for the Innotemp web interface.
    *   **Password:** Your password for the Innotemp web interface.
5.  Click "Submit".

The integration should now be configured and your Innotemp sensors and controls should appear in Home Assistant. If the controller is offline when Home Assistant starts, setup is retried automatically.

### Debug logging

To see the traffic between Home Assistant and the controller, enable debug logging in `configuration.yaml` (passwords are masked):

```yaml
logger:
  default: warning
  logs:
    custom_components.innotemp: debug
```

## Testing

The API this integration talks to is documented in [`docs/API_SPEC.md`](docs/API_SPEC.md). There are three levels of tests:

1.  **Unit tests** (mocked HTTP), part of `pytest tests/`.
2.  **Fake-server tests**, also part of `pytest tests/`. `tests/fake_innotemp.py` is a small aiohttp server that behaves like the controller: login with a `PHPSESSID` cookie, redirect to the login page when the session has expired, roomconf, signal names, the SSE stream, and `value.save.php` with `val_prev` checking. `tests/test_fake_server_integration.py` runs the real API client and the whole integration inside Home Assistant against it. The responses come from `tests/fixtures/synthetic/`, which is **synthetic** (written from the spec, not recorded). When `tests/fixtures/recorded/` exists, the tests also run against it.
3.  **Live smoke test** against a real controller (see below). The unit and fake-server tests cannot replace this step.

```bash
pip install -r requirements.txt
pytest tests/            # or ./run_tests.sh
black --check .
```

### Testing against a real controller

`scripts/smoke_test.py` uses the integration's own API client without Home Assistant (it only needs `aiohttp`). It:

*   logs in and checks that the session cookie was kept,
*   fetches the configuration and lists the entities the integration would create per platform,
*   reads a few SSE messages and checks that each has one value per signal name,
*   warns about entities that will stay "unknown" (neither a live signal nor a value in the configuration).

It does not change anything on the controller unless you pass `--write-test`.

```bash
python scripts/smoke_test.py --host 192.168.25.204 --user USER --password PASS
```

Options:

*   `--record tests/fixtures/recorded/` saves the responses as a fixture set for the fake-server tests. Passwords, the username, cookies and location fields (`city`) are replaced with `REDACTED`. Review the files before committing them.
*   `--write-test PARAM` performs **one write** to that parameter id, after asking for confirmation (`--yes` skips the prompt). By default it writes the current value back unchanged, which tests the write path without changing the heating. Add `--write-value V` to write a different value; the previous value is restored afterwards unless you pass `--no-restore`.
*   `--messages N` sets how many SSE messages to read, `--show N` how many entities to list per platform, and `-v` turns on debug logging.

The script exits with status 0 if all checks passed. Please attach its output (it contains no credentials) when reporting a problem.
