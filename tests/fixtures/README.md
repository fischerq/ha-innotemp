# Controller response fixtures

Each subdirectory is one fixture set that `tests/fake_innotemp.py` serves:

| File | Endpoint |
| --- | --- |
| `groups.read.json` | login response body |
| `roomconf.read.json` | configuration (`roomconf.read.php`) |
| `live_signal.read.json` | signal names (`live_signal.read.php`, `init=1`) |
| `live_signal.read.SSE.txt` | raw SSE stream body, replayed in a loop |
| `value.save.json` | optional: response to a successful write |
| `source.json` | where the set came from |

- `synthetic/` is **SYNTHETIC**: hand-written from `docs/API_SPEC.md` and
  `tests/roomconf_test_data.json`, not recorded from a controller. Where the
  real device stores current values in roomconf, the exact SSE framing and the
  `value.save.php` response body are guesses.
- `recorded/` (absent until someone records it) is produced by
  `python scripts/smoke_test.py --host ... --record tests/fixtures/recorded/`
  against a real controller. Passwords, the username, cookies and the `city`
  field are stripped. Once it exists, `tests/test_fake_server_integration.py`
  also runs its read-path test against it. Review the files before committing
  them.
